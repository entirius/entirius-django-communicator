# Changelog

## Unreleased

### Added

- **Sequence autostart.** `Sequence.autostart` (at most one per channel, partial unique constraint, migration 0012):
  the first delivered templated message of an open thread that never had a sequence state starts the channel's
  active autostart sequence. A hand-written message (no template version) never starts one, and a thread that ever
  had a state is never restarted. `POST sequences/` accepts `autostart`; new `PATCH sequences/<id>/`
  (`is_active`, `autostart`); a second autostart sequence answers 409 `DUPLICATE_AUTOSTART`.
- **One-click unsubscribe (RFC 8058).** With `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` set every mail carries
  `List-Unsubscribe` (one unfolded line) and `List-Unsubscribe-Post`, and a link in the text and HTML parts labelled
  by the body language. Public route `api/communicator/v2/public/unsubscribe/<token>/` (signed token, no expiry):
  GET shows a confirmation page, POST suppresses the address on the channel (reason `unsubscribe link`), stops its
  sequences, sends `optout_confirmed` per `subject_ref` and records the agreements objection; idempotent; an invalid
  token is 400; throttled per client address (scope `communicator_unsubscribe`, fallback `60/min`).
- **Preflight.** `preflight_service.problem` checks the legal footer at accept (409 `REVIEW_REFUSED`) and again at
  delivery (`failed/legal_footer`, new `FailureCode.LEGAL_FOOTER`, migration 0014). A message without a template
  version always needs one.
- **Spam complaints.** With `COMMUNICATOR_COMPLAINT_FEEDBACK` an ARF report (`abuse`, `fraud`) for one of our
  messages is stored as `Reply(kind=complaint, matched_by=feedback)`; on a live channel the recipient is suppressed,
  the sequence stops (`complaint`) and a `high` alert goes out (migration 0013).
- **Signal `message_bounced(message, hard, reason="")`**, on commit, live channels only: hard DSN, SMTP 5xx recipient
  refusal, complaint (`reason="complaint"`).
- **Approval inbox across channels.** `GET admin/approvals/` (filters `channel`, `template_key`, `search`,
  `min_age_hours`; `counts.by_channel`; a `blocker` per row), `POST admin/approvals/bulk/` (approve / reject, at most
  `COMMUNICATOR_BULK_APPROVAL_MAX` = 100 ids, each id on its own, `done` / `failed`), `GET admin/approvals/log/`;
  model `BulkApprovalLog` (migration 0015). Area `communicator.review`. System check `communicator.reserved_idx`
  refuses a channel idx `approvals`.
- **Mailboxes.** `Mailbox` replaces `MailboxConfig` (migrations 0016–0018; the old row becomes the channel's default
  mailbox, receiving as before, `send_enabled = false`): several per channel, each with its From address, display
  name, optional CC, its own SMTP and IMAP login (encrypted, write-only), daily cap with warm-up and pause.
  `communicate(..., mailbox_user_id=None)` picks a new thread's mailbox (the user's, else the default, else none =
  the channel SMTP entry); `Thread.mailbox` keeps it. A mailbox that is paused, disabled or `needs_login` holds its
  own mail; the others keep sending. Per-mailbox Redis counter `communicator:mailbox:<id>:<day>`. Approval of a
  draft whose mailbox cannot send is refused (new `FailureCode.MAILBOX`). Admin API `mailboxes/` (CRUD, `test/`,
  `pause/`, `resume/`), `health/`, `health/policy/`, `health/log/`.
- **Sending health.** `HealthPolicy` (per channel, off by default) pauses a mailbox of a live channel over the
  bounce, opt-out or complaint limit (minimum sample); `MailboxPauseLog` records every pause and resume; only a person
  resumes.
- Approval inbox: filter `mailbox`, `counts.by_mailbox`.
- **Suppression scope.** `COMMUNICATOR_SUPPRESSION_SCOPE = "channel"` (default) or `"install"`: under `install`
  every new `email` / `domain` suppression (manual, opt-out confirm, unsubscribe link, bounce, complaint) is a global
  row (`channel = NULL`) and holds in every channel; global rows are honoured under both settings and a switch
  rewrites nothing. `suppression_service.scope()`; command `manage.py communicator_suppressions --to-install`
  (dry run unless `--apply`, idempotent) copies channel rows to global rows. Check constraint: a global row only for
  kinds `email_token`, `email`, `domain` (migration 0019).
- **`Suppression.source`**: `manual`, `optout_confirmed`, `unsubscribe_link`, `bounce`, `complaint`, `erased`; the data
  migration derives it from the old `reason` text where it is unambiguous, else `manual`; `reason` stays a free note.
- **Address status.** `AddressStatus` (one row per address, install-wide: `unknown`, `valid`, `risky`, `invalid`, a
  reason code, `verified_at`); a live hard bounce (DSN, SMTP 550/551/553/554 on RCPT) writes `invalid / hard_bounce`,
  final. `address_service`: `status_of`, `verify` (syntax → disposable domain → DNS, injected resolver, no SMTP
  probe), `mark_hard_bounce`, `stale`, `reverify_stale`. Off until `COMMUNICATOR_VERIFY_ENABLED`; then
  `communicate()` verifies an unknown or stale address (`COMMUNICATOR_VERIFY_MAX_AGE_DAYS`, 30) before it drafts and
  the daily task `django_communicator.reverify_addresses` re-checks up to `COMMUNICATOR_VERIFY_BATCH` (200).
  `COMMUNICATOR_DISPOSABLE_DOMAINS`; optional extra `verify` (dnspython).
- **One question: `suppression_service.can_mail(channel, email)`** → `address_invalid`, `suppressed` or None (at most
  two queries), asked by `communicate()`, the delivery before its claim and the draft retry; a refusal is a
  `suppressed` message with `failure_detail` = the code. `explain(channel, email)` adds the matching rows and the
  address status.
- Admin API: `GET addresses/status/?email=` (the `explain` answer); `GET suppressions/` filters `scope`, `source`,
  `search` and every row carries `scope` and `source`.
- **Reply classes.** A human reply may get one of seven classes (`interested`, `not_now`, `not_interested`,
  `out_of_office`, `wrong_person`, `unsubscribe`, `unclear`) from one toolbox completion (tags
  `communicator.reply_class`, `channel:<idx>`, temperature 0, closed JSON schema, reply text only as data). It runs
  only with an active per-channel `ReplyClassProfile` (prompt, model, `min_confidence` 0.7, `max_chars` 3000) and the
  toolbox enabled; a starting prompt ships in `fixtures/reply_class_profile.json`, never in code. Any failure means
  no class and the behaviour as before. Stored on `Reply`: `intent`, `intent_confidence`, `intent_source`,
  `intent_return_date`, `needs_review`, `plan_applied` (migration 0020).
- **Plan per class.** `ReplyClassPlan(channel, intent)`: sequence `stop` / `pause` / `pause_until_return`, alert
  severity, `needs_review`; no row = the code default (migration 0021). `unsubscribe` is always a suspected opt-out a
  person confirms; a confident `not_interested` wins over an opt-out phrase, any other confident class with a phrase
  hit is a suspected opt-out. No plan suppresses, reminds or moves a stage.
- **Resume after an out-of-office reply.** `ThreadSequenceState.resume_at` (the day after the return date, channel
  timezone) and beat task `django_communicator.resume_paused_sequences` (hourly, see `docs/install.md`): resumes a
  due state still paused, in a thread not closed, to an address that may be mailed.
- Admin API: `GET/PUT reply-classes/profile/` (area `communicator.content`), `GET reply-classes/plans/`,
  `PUT/DELETE reply-classes/plans/<intent>/` (area `communicator.settings`); `GET replies/` filters `intent` and
  `needs_review`; replies, thread rows (`reply_intent`, `needs_review`) and timeline entries carry the class.
  Development only: `POST test/ingest-reply/` ingests a reply with a given class.

### Changed

- **`inbound_service.ingest` is no longer one transaction:** the reply class call runs first, outside any
  transaction; the writes of one reply, a DSN and a complaint stay atomic each. A plain reply's alert severity comes
  from the plan of its class (`high` without one, as before).

- **Opt-out phrases match on folded text** (lower case, no diacritics, `ł` → `l`, other characters → one space), so a
  phrase is a stem; wider default `pl` and `en` lists. A hit is still a suspicion a person confirms or dismisses.
- `suppression_service.suppress_email` returns whether it wrote a row; `optout_service.record_objection` is public.
- A missing channel SMTP entry defers each message on its own (alert once a day) instead of stopping the channel's
  run; a refused mailbox login holds that mailbox's mail (`needs_login`, `high` alert once a day).
- The IMAP poll alert is per mailbox (`IMAP poll of mailbox <address> failed`), so a second broken mailbox is
  announced.
- Suppression routes moved to area `communicator.settings` (was `communicator.conversations`); `POST suppressions/`
  accepts kinds `email` and `domain` only (an `email_token` row could match nothing) and follows the scope;
  `DELETE suppressions/<id>/` also removes a global `email` / `domain` row (a token row stays a 404).
- Health opt-out counts read `Suppression.source` (`optout_confirmed`, `unsubscribe_link`), global rows included,
  instead of the `reason` text.
- GDPR erasure and the `contact_anonymised` receiver drop the address's `AddressStatus` row; the export lists it.
- `docs/openapi.yaml` regenerated.

### Removed

- `MailboxConfig` and the singular `GET/PUT <channel_idx>/mailbox/` routes — use `mailboxes/`.

## 0.4.1 — 2026-11-04

### Security

- Admin API refusals answer with the text the module wrote, never with `str(exc)` (CodeQL
  `py/stack-trace-exposure`, 12 alerts). Refusals the module writes for a client derive from
  `services.exceptions.ClientError` and the views answer with its `client_message`, never `str(exc)`:
  `InvalidTransitionError`, `ReviewError`, `OptoutStateError`, `SequenceError`, `FooterError`, `RenderError`,
  `LegalFooterRequiredError`, `DuplicateSuppressionError`, `DuplicateSequenceError`, `ChannelConfigError` and the
  new `InvalidSuppressionValueError`. Any other exception reaches the v2 handler: 500 `INTERNAL_ERROR` with a
  `debug_id`, the traceback only in the log. Unchanged on purpose: Django model validation messages (`PATCH channel/`,
  template save) and toolbox errors (`handle_toolbox_error` in django-utils) keep their texts.
- CI workflow token is read-only (`permissions: contents: read`; CodeQL `actions/missing-workflow-permissions`).

### Changed

- `POST suppressions/`: only the module's `InvalidSuppressionValueError` is a 400 on `value` (same messages:
  "not an email address", "a domain has no @", "not a domain"); any other `ValueError` from below is now a 500
  `INTERNAL_ERROR` instead of a 400 with its text. It is still a `ValueError`, so callers that catch that keep
  working. Every other status, `error` code and message is unchanged.
- `POST suppressions/` with a NUL byte in `value` is a 400 on `value` ("not an email address" / "not a domain")
  instead of a 500 from the database.

## 0.4.0 — 2026-10-12

- **Toolbox switched off (`AI_TOOLBOX_ENABLED = False`).** An AI template is refused before anything is written:
  `communicate()` and `review_service.rewrite()` raise `ToolboxDisabledError` (no thread, message, version, alert or
  counted rewrite); `POST review/<id>/rewrite/` and `POST test/communicate/` answer 503 `AI_TOOLBOX_DISABLED`, as
  `test-generate/` and `models/` do. Static templates, suppression and the no-template failure are unchanged. A due
  AI follow-up keeps its step and due date and is created once the switch is on; `retry_failed_drafts` makes no
  call. Callers of `communicate()` must handle the new exception. An enabled but unconfigured toolbox behaves as
  before.
- Access: the module declares its own access areas on its AppConfig and its admin views (copied from the
  entirius-django-access defaults; behaviour unchanged).
- Access: `POST templates/<id>/test-generate/` needs `communicator.content:write` (it calls the paid AI toolbox; it
  was a read); the `communicator.review` area is flagged `pii` (same as the access catalogue).
- OpenAPI: tags in Title Case (`Communicator Footers`, `Inbox`, `Review`, `Sending`, `Sequences`, `Suppressions`,
  `Templates`), also in `docs/openapi.yaml`. Metadata only; a client that groups by tag name sees the new names.
- Requires `entirius-django-utils` 2.3.0 or later (the AI toolbox switch and `ToolboxDisabledError`).

## 0.3.0 — 2026-09-30

- **HTML mail footer per channel and language.** `MailFooter` (migration `0011`, also `Message.footer_html`):
  the layout around the legal text, with `{{ legal }}` exactly once as text — inside an attribute (`href`, `alt`)
  it is refused with 400 on `html`, since the recipient would never see the legal text; sanitised on save with
  `nh3` (new dependency, already in the Volkanos lock via django-utils-translator), style values with `url(` or
  `expression(` dropped. Admin API `GET footers/`, `GET/PUT/DELETE footers/<language>/`. `mail_builder` resolves
  the footer of the body's language (the template version the message was rendered from; the thread language when
  it has none), else the channel default language's, else none (legal text alone, as before); the text part is the footer as plain text. The sent
  message stores the rendered footer in `footer_html` (exported and erased by `gdpr.py`); `MessageResponse` shows it.
- **Pool texts can be edited and removed.** `PATCH sequences/<id>/texts/<text_id>/` (`body`, `is_active`) and
  `DELETE` on the same path: a text no thread used is deleted (204), a used one deactivated (200 with the row), so
  the thread history stays.
- **Inbox list of conversations.** `GET conversations/` (`state`, `page`, `page_size`): one row per `subject_ref`,
  the newest thread's row plus `thread_count`, `replied` and the draft's own `recipient_email`; the state, `draft`,
  `waiting` and `last_text` come from any thread of the conversation, ordering by its latest activity, `counts` per
  state count conversations. Three queries for a page's rows. `threads/` is unchanged.
- **Inbox list over `threads/`.** `state` filter (`draft`, `waiting`, `replied`), `sort=activity` (latest mail, draft
  or reply first; the default stays newest created, which a caller's "newest thread" relies on), `counts` per state
  in every page, and per row `activity_at`, `subject`, `last_text`, the newest `draft` and the first `waiting` mail
  with its `next_slot` — two queries for a page's rows instead of one detail call per thread.
- **Templates target an audience.** `MessageTemplate.audience` (upper-case code, blank = every audience; migration
  `0010`), unique `(channel, key, language, audience)`. `template_service.resolve` and `communicate(audience=…)`
  walk (language, audience) → (language, blank) → (default language, audience) → (default language, blank). The
  caller owns the meaning — leads passes the lead type. Template admin API reads and writes `audience`; a PUT
  without it keeps the stored value. `test/communicate/` takes `audience`.
- **SMTP health check.** System check `communicator.smtp` (tag `entirius_config`): a channel outside `dry_run`
  without its `EMAIL_SMTP_CONFIGURATION_CHANNELS` entry — the same test `mail_builder.build` makes, visible before
  the first send (django-munin `health/`, or `manage.py check --database default --tag communicator.smtp`). Reads
  the DB only when `databases` is passed, so boot stays quiet. The runtime `smtp_missing` alert stays as the
  safety net.

## 0.2.0 — 2026-09-16

- **Draft recovery after a toolbox outage.** New beat task `django_communicator.retry_failed_drafts` (host
  schedule every 10 min, queue `communicator_default`): once `status()` reports the toolbox reachable, a
  first-version AI draft that failed transiently (`ToolboxConnectionError`, timeout, HTTP 5xx) is generated again
  from its stored prompt, at most `COMMUNICATOR_DRAFT_RETRY_LIMIT` (3) times; success → `review_required`, never
  sent without review. Budget, model and schema failures stay failed. Migration `0009`: `Message.draft_retries`.
  A transiently failed draft now keeps `rendered_prompt`; every retry appends a line to `failure_detail`.
- Development endpoints `test/retry-drafts/` and `test/toolbox-outage/` (the `django_utils.toolbox.outage`
  switch — needs `entirius-django-utils` with the outage switch).
- Draft retries ask the subject owner first: new synchronous signal `draft_retry_requested(sender=Message,
  message=...)`; a returned reason ends the retries without spending one (`failure_detail` line
  `retry: blocked_by_subject <reason>`). `test/toolbox-outage/` rejects unknown keys with 400.

## 0.1.0 — 2026-09-15

Initial release. Channel-agnostic communication for Volkanos: a caller asks for a message by template key
and gets a reviewed, policy-bound, single delivery and the replies that come back.

- **Templates and drafts.** `MessageTemplate` per channel, key and language (`static` or `ai_prompt`) with
  immutable versions; `communicate()` — suppression, language fallback, required legal footer, render, AI
  drafts over the AI toolbox with typed failure codes (`budget`, `schema`, `model`, `upstream`) and
  notifications. Covers C-01…C-06, C-10…C-12, C-29, C-33.
- **Review.** Accept, rewrite with notes (automated rewrites capped), manual edit, skip, skip company;
  `message_approved` and `company_skipped` signals. Covers C-07…C-09.
- **Sending.** Channel modes `dry_run` / `sandbox` / `live` with the live double gate
  (`ENVIRONMENT=production` + `live_enabled` + `mode=live`); `SendPolicy` with hour windows, business days,
  channel-country holidays and a Redis daily cap spread over the day; the `send_due` beat under celery-once
  with a durable send-once claim; multipart mail with Message-ID and References on the django_email channel
  connection; SMTP 4xx retry, 5xx failure, live-only suppression on RCPT refusal; send now. Covers C-13…C-19,
  C-30…C-32.
- **Sequences.** Follow-up steps over text pools, re-armed or stopped by every delivery outcome, reply and
  opt-out. Covers C-27, C-28.
- **Inbound.** Read-only IMAP poll (`poll_inbox`, UID cursor, UIDVALIDITY, quarantine) into `Reply` rows:
  header and sender thread matching, autoresponders, suspected opt-outs with confirm / dismiss (suppression,
  `optout_confirmed`, agreements objection), DSN hard and soft bounces, idempotent by inbound Message-ID;
  `MailboxConfig` with an encrypted password. Covers C-20…C-26.
- **Retention and GDPR.** `contact_anonymised` receiver (soft django_leads dependency), `gdpr.py` export and
  erase scoped to the subject's own replies, global `email_token` suppression of erased and anonymised
  addresses (no plain address kept). Covers L-16, L-17.
- **Admin API v2** under `api/communicator/v2/admin/<channel_idx>/` (JWT + `IsAdminUser`): review, templates,
  models, suppressions, channel mode, policy, outbox, sequences, threads with timeline, replies, mailbox;
  development-only `test/` endpoints for BDD and e2e.
- **Pre-release fixes.** `PATCH channel/` answers 400 to `mode=live` or any `live_enabled` — the live double
  gate is set in Django admin only. An alert whose notifications channel (same idx) is missing or failing is
  logged at ERROR with both idx values instead of a warning. Every 409 carries a distinct `error` code
  (`ALREADY_REVIEWED`, `REVIEW_REFUSED`, `NOT_WAITING`, …, listed in `docs/api.md`; needs django_utils 2.1.0).
  `GET review/<id>/` reads one message of the channel; timeline entries carry `message_id`.
- Module docs (`docs/api.md`, `concept.md`, `install.md`, `operations.md` with the go/no-go checklist for live
  channels, `testing.md`, `gotchas.md`), `docs/openapi.yaml` and ERD config (`docs/erd-config.yaml`).
