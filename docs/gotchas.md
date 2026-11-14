---
title: Gotchas
description: The one list of rules that bite — read before touching statuses, sending, sequences, inbound or GDPR.
---

Install-time traps (once backend, queues, OAS 3.1, `SECRET_KEY`, SMTP channel config) live in
`install.md`. Each item: the rule, then where it is enforced.

## Templates

- **The audience cascade is specific-first, never "best match".** `resolve` tries (language, audience),
  (language, blank), (default language, audience), (default language, blank) and stops at the first active
  template. A variant in another language never beats a blank-audience template in the recipient's language.
  An audience variant without a blank sibling makes every other audience fail `no_template`.
- **Omitted `audience` on `PUT templates/<id>/` keeps the stored one** — an older editor that does not know the
  field cannot turn a variant into the default (and collide with it on the unique key).

## Statuses and review

- **Only `message_service` writes `Message.status`.** Views, tasks and other services go through
  `transition` — a compare-and-set against `MESSAGE_STATUS_TRANSITIONS`; a stale copy raises
  `InvalidTransitionError` (409 in the API).
- **`message_approved` and `company_skipped` fire on commit** — a receiver never sees an uncommitted row.
- **Automated rewrites are counted on the locked row before the toolbox call** — failures count, so a
  failing model cannot loop past `COMMUNICATOR_AUTOMATED_REWRITE_LIMIT`.
- **Static templates and suppressed recipients never reach the toolbox; `complete()` is never retried** —
  it is paid and non-idempotent.
- **A switched-off toolbox (`AI_TOOLBOX_ENABLED = False`) is refused before anything is written.** For an AI
  template `communicate()` and `review_service.rewrite()` raise `ToolboxDisabledError`: no thread, message, version
  or alert. Every caller of `communicate()` must handle it; the API answers 503 `AI_TOOLBOX_DISABLED`.
- **`failure_detail` holds error class, toolbox code, HTTP status and field names — never the prompt.** Its
  last line decides whether a draft is retried (`drafting_service.is_transient`) — keep the `failure_detail()`
  format, and append retry lines, never rewrite them.
- **A draft retry is a new whole generation, not a `complete()` retry.** `retry_failed_drafts` counts it on the
  row (`draft_retries`, compare-and-set) before the call, holds no lock or transaction during it, and only
  `message_service.recover_draft` moves `failed → review_required`. Only transient failures keep
  `rendered_prompt` on a failed draft — that stored prompt is what the retry sends.
- **A template content change goes through `template_service.save_template`** (Django admin included) —
  a direct `save()` would change content without a version and break C-29.

- **A hand-written message (`compose_service.compose`) is approved by its author and never routed through
  `review_required`.** It stops the thread's sequence (`manual`); `sequence_service.on_delivered` ignores any message
  without a template version, and follow-ups read context and footer from the newest templated message only.
- **The preflight runs at accept and again at delivery** (`preflight_service.problem`). A message without a
  template version always needs a legal footer; the approval inbox shows the same detail as `blocker`. Bulk
  approve goes through `review_service.accept`, so the preflight is never skipped. It also asks the owner of the
  subject (`recipient_check`) on every approval row: a receiver must stay a constant number of queries.
- **The approval routes are declared before the `<channel_idx>/` include** (`urls.py`). A channel idx `approvals`
  would be unreachable; `communicator.reserved_idx` (an error, DB-bound) names it.

## Sending

- **`delivery_service.deliver` has one caller, `send_service`.** `tests/test_sending.py` greps for
  `deliver(`; a second caller would bypass the policy, the cap and celery-once.
- **The send-once claim runs in a durable transaction** — inside an outer `atomic` block it raises
  `RuntimeError`. A view that triggers sending must be `non_atomic_requests` (the dev `send-due` view is).
- **A `sending` row is never re-sent.** After `COMMUNICATOR_SENDING_STALE_MINUTES` it becomes
  `failed/send_outcome_unknown`. Do not "fix" this into a retry: at most once beats at least once.
- **Every sending decision reads `clock_service.now_for(channel)`**, never `timezone.now()` — the dev clock
  override and the channel timezone depend on it.
- **The live gate is checked per message on the channel re-read with the claim**; the run-level check is a
  shortcut only. No setting may relax `ENVIRONMENT == "production"` (a test asserts it).
- **Only RCPT refusals (550/551/553/554) suppress, and only in live.** `SMTPSenderRefused` /
  `SMTPDataError` are our problem, not the recipient's.
- **A mailbox holds its own mail; it never stops the run.** Paused, disabled or `needs_login` → `deferred`; a missing
  or refused login (and a channel without its SMTP entry) is an outcome of that one message. Never turn it back into
  a channel-wide abort: one broken mailbox would stop the others.
- **The login test logs in through the host's `EMAIL_BACKEND`**, the connection a send uses — never a fixed SMTP
  backend (a host wrapper such as zeno's GreenMail backend would disagree with it). A host backend that does not
  speak SMTP (console, locmem) logs nobody in, so the test answers `ok` there.
- **`Thread.mailbox` is chosen once**, before the thread's first message, and never changes — a thread keeps its
  From. A thread without one sends through the channel SMTP entry. The FK is `PROTECT`: delete refuses a used one.
- **Mailbox writes name their columns.** `mailbox_service.update_mailbox` (and the Django admin through it) never
  writes a password column unless a new password is sent: an undecryptable password reads as `""`, and a full
  `save()` would destroy the ciphertext.
- **A channel without a `SendPolicy` never sends** — `run_send_due` iterates policies, not channels.
- **`COMMUNICATOR_SEND_INTERVAL_MIN` must match the beat crontab** — the spread quota counts runs from it.
- **A bad stored `country` / `timezone` answers 409 `CHANNEL_CONFIG_INVALID`** from every admin view that
  loads the policy, never 500; `Channel.clean()` and `save()` validate both.

- **`List-Unsubscribe` is written unfolded** by `mail_builder._UnsubscribeMail`; only that header gets the custom
  header class. Test it on the wire (`message(policy=email.policy.SMTP).as_bytes()`), not on the parsed message.
- **The unsubscribe token has no expiry and is tied to the channel idx** — renaming a channel invalidates every link
  already sent (400). `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` is read at import, like every module setting.
- **The unsubscribe throttle falls back to `UnsubscribeThrottle.fallback_rate`** when `DEFAULT_THROTTLE_RATES` has
  no valid `communicator_unsubscribe` rate; the fallback is not a class `rate`, which would make DRF ignore the
  setting. `get_rate()` never returns `None`.

## Sequences

- **Follow-ups carry `Message.sequence_step`**, inherited by edited and rewritten versions; the step
  advance is a compare-and-set on `step`, so overlapping runs create one follow-up.
- **Every terminal outcome of a follow-up goes through `sequence_service.on_follow_up_finished`** —
  delivered, failed, suppressed, rejected, skipped. A new terminal path that skips it strands the sequence.
- **Follow-ups resolve without an audience.** A step's template is looked up with a blank audience (the thread
  keeps none), so an audience-only template key never serves a sequence step — keep a blank-audience variant.
- **A used pool text is deactivated, never deleted** (`sending_config_service.remove_text`). `ThreadPoolUsage`
  cascades from the text; losing it lets the pool give a thread the same follow-up twice. The row lock in
  `remove_text` blocks a concurrent usage insert between the check and the delete.
- **Step templates are resolved with `requires_review=False`** — only an `auto_approve` static template
  sends without review; anything else lands in the review queue.

- **Autostart fires once per thread, ever.** `sequence_service._autostart` skips a thread with any
  `ThreadSequenceState` row and a message without a template version; a stopped thread is never restarted.

## Footer

- **`{{ legal }}` goes in a block of its own** (`<div>`, `<td>`), never inside a `<p>`: it is replaced by
  `<p>` paragraphs, and a paragraph inside a paragraph breaks in mail clients.
- **The footer is resolved at send, not at `communicate()`** — an edit reaches every mail not yet sent; the
  sent row keeps `footer_html`. A soft-bounce retry re-resolves (nothing is stored until `sent`).
- **Save goes through `footer_service.save_footer`** — the sanitiser and the placeholder rule live there. There is
  no Django admin page for `MailFooter` on purpose: a raw admin save would skip both.

## Inbound

- **`Thread.status` is written only by `inbound_service`** (and a close action). A closed thread stays
  closed: the reply is stored, the status untouched.
- **A DSN is only a top-level `multipart/report; report-type=delivery-status`** — a reply forwarding a
  bounce is a reply. DSNs never change `Thread.status`.
- **An opt-out phrase is a suspicion, not a suppression.** Only `confirm-optout` suppresses. A dismissed
  suspicion keeps the sequence paused until `threads/<id>/resume-sequence/`.
- **The cursor moves only past ingested, dropped or quarantined mail.** Transient failures leave it; a
  poll that swallowed them would lose mail.
- **Each mailbox is polled and announced on its own** (alert kind `imap:<mailbox id>`); dedup, own-mail check and
  thread matching stay per channel, so a reply that reached two mailboxes is ingested once.
- **Credentials and bodies are never logged; `Reply.raw_headers` holds headers only; attachments are
  never stored.**

- **Opt-out phrases are stems on folded text** (`inbound_service.fold`), matched anywhere in the first 2 000
  characters above the quote: a very short phrase over-matches. A hit is a suspicion only.
- **Complaints need `COMMUNICATOR_COMPLAINT_FEEDBACK`**; off, an ARF report goes down the normal path and is usually
  dropped as unmatched. A sandbox or dry-run complaint is stored and changes nothing.
- **`message_bounced` is live-only** — a sandbox bounce or complaint says nothing about the real recipient.
- **`ingest` is not one transaction.** The reply class toolbox call runs before the writes and outside any
  transaction; the writes of one reply (`_store_human_reply`), a DSN and a complaint are atomic each. Never wrap
  `ingest` in `atomic` — a slow toolbox would hold the transaction open. A poll that stored the same mail meanwhile
  wins (re-checked inside the write).
- **A reply class is advice.** Every failure ends in the unclassified behaviour (sequence stopped, `high` alert),
  never in an exception or a lost reply. The prompt lives in `ReplyClassProfile`, never in code; prompts and reply
  text are never logged. No plan suppresses: the class `unsubscribe` is a suspected opt-out a person confirms.
- **`stop_sequence` drops `resume_at`.** That is how a newer reply cancels the resume of an out-of-office pause;
  `resume_paused_sequences` resumes only a state still paused with `resume_at` due.

## Admin API errors

- **A view answers a refusal with `exc.client_message`, never `str(exc)`.** Only refusals the module writes for a
  client derive from `services.exceptions.ClientError`; a view catches those types alone, and everything else goes
  to the v2 handler (500 `INTERNAL_ERROR`, `debug_id`). `raise Conflict(str(error))` is still an exception-text
  leak to CodeQL (`py/stack-trace-exposure`). A new refusal type derives from `ClientError` (and from `ValueError`
  when existing callers catch that); a service that wraps another refusal passes its `client_message` on.
  `tests/test_v2_error_exposure.py` pins it per route (add a case with a new refusal); the CodeQL scan is the
  net for routes it does not list.

## Module boundaries and GDPR

- **`utils/domains.py`, `utils/emails.py`, `utils/encryption.py` + `encrypted_field.py` are copies** (of
  leads/siteintel, django_leads and contact_forms) — never import them across modules.
  `test_token_parity_with_django_leads` guards the email token copy when leads is installed.
- **Global suppression stores the token, never the plain address** (`Suppression(kind=email_token,
  channel=None)`); a token address is always suppressed. Under `COMMUNICATOR_SUPPRESSION_SCOPE = "install"` global
  `email` / `domain` rows do carry the address — they are the subject's data (export) like channel rows.
- **Ask `suppression_service.can_mail`, never `is_suppressed` alone** — the address status (`invalid`) blocks too.
  The scope comes from the setting only (`suppression_service.scope()`), never from the rows.
- **`AddressStatus` is install-wide** and only a live bounce writes it — a sandbox or dry-run bounce says nothing
  about the real address. Verification never runs inside a transaction or lock (DNS is slow).
- **A subject's data is threads to the address and replies *sent by* it** — a colleague's reply in the
  same thread is neither exported nor rewritten.
- **Alerts need a django_notifications channel with the communicator channel's `idx`** — otherwise they
  are lost with a log warning only.
- **Workers have no autoreload** — restart after changing a task or what it calls.
