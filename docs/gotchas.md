---
title: Gotchas
description: The one list of rules that bite — read before touching statuses, sending, sequences, inbound or GDPR.
---

Install-time traps (once backend, queues, OAS 3.1, `SECRET_KEY`, SMTP channel config) live in
`install.md`. Each item: the rule, then where it is enforced.

## Statuses and review

- **Only `message_service` writes `Message.status`.** Views, tasks and other services go through
  `transition` — a compare-and-set against `MESSAGE_STATUS_TRANSITIONS`; a stale copy raises
  `InvalidTransitionError` (409 in the API).
- **`message_approved` and `company_skipped` fire on commit** — a receiver never sees an uncommitted row.
- **Automated rewrites are counted on the locked row before the toolbox call** — failures count, so a
  failing model cannot loop past `COMMUNICATOR_AUTOMATED_REWRITE_LIMIT`.
- **Static templates and suppressed recipients never reach the toolbox; `complete()` is never retried** —
  it is paid and non-idempotent.
- **`failure_detail` holds error class, toolbox code, HTTP status and field names — never the prompt.** Its
  last line decides whether a draft is retried (`drafting_service.is_transient`) — keep the `failure_detail()`
  format, and append retry lines, never rewrite them.
- **A draft retry is a new whole generation, not a `complete()` retry.** `retry_failed_drafts` counts it on the
  row (`draft_retries`, compare-and-set) before the call, holds no lock or transaction during it, and only
  `message_service.recover_draft` moves `failed → review_required`. Only transient failures keep
  `rendered_prompt` on a failed draft — that stored prompt is what the retry sends.
- **A template content change goes through `template_service.save_template`** (Django admin included) —
  a direct `save()` would change content without a version and break C-29.

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
- **A channel without a `SendPolicy` never sends** — `run_send_due` iterates policies, not channels.
- **`COMMUNICATOR_SEND_INTERVAL_MIN` must match the beat crontab** — the spread quota counts runs from it.
- **A bad stored `country` / `timezone` answers 409 `CHANNEL_CONFIG_INVALID`** from every admin view that
  loads the policy, never 500; `Channel.clean()` and `save()` validate both.

## Sequences

- **Follow-ups carry `Message.sequence_step`**, inherited by edited and rewritten versions; the step
  advance is a compare-and-set on `step`, so overlapping runs create one follow-up.
- **Every terminal outcome of a follow-up goes through `sequence_service.on_follow_up_finished`** —
  delivered, failed, suppressed, rejected, skipped. A new terminal path that skips it strands the sequence.
- **Step templates are resolved with `requires_review=False`** — only an `auto_approve` static template
  sends without review; anything else lands in the review queue.

## Inbound

- **`Thread.status` is written only by `inbound_service`** (and a close action). A closed thread stays
  closed: the reply is stored, the status untouched.
- **A DSN is only a top-level `multipart/report; report-type=delivery-status`** — a reply forwarding a
  bounce is a reply. DSNs never change `Thread.status`.
- **An opt-out phrase is a suspicion, not a suppression.** Only `confirm-optout` suppresses. A dismissed
  suspicion keeps the sequence paused until `threads/<id>/resume-sequence/`.
- **The cursor moves only past ingested, dropped or quarantined mail.** Transient failures leave it; a
  poll that swallowed them would lose mail.
- **Credentials and bodies are never logged; `Reply.raw_headers` holds headers only; attachments are
  never stored.**

## Module boundaries and GDPR

- **`utils/domains.py`, `utils/emails.py`, `utils/encryption.py` + `encrypted_field.py` are copies** (of
  leads/siteintel, django_leads and contact_forms) — never import them across modules.
  `test_token_parity_with_django_leads` guards the email token copy when leads is installed.
- **Global suppression stores the token, never the plain address** (`Suppression(kind=email_token,
  channel=None)`); a token address is always suppressed.
- **A subject's data is threads to the address and replies *sent by* it** — a colleague's reply in the
  same thread is neither exported nor rewritten.
- **Alerts need a django_notifications channel with the communicator channel's `idx`** — otherwise they
  are lost with a log warning only.
- **Workers have no autoreload** — restart after changing a task or what it calls.
