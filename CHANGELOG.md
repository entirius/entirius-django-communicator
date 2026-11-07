# Changelog

## Unreleased

### Changed

- **Opt-out detection (outbound roadmap F4).** `COMMUNICATOR_OPTOUT_PHRASES` are matched on folded text and the
  default lists are wider (Polish stems such as "nie kontaktowa", "usunięcie mnie", "rezygnuj"; English variants).
  Natural refusals that used to be stored as a plain `reply` now pause the sequence as a suspected opt-out.
  Replies stay suspicions until a person decides.

### Fixed

- **`List-Unsubscribe` on the wire.** The signed URL is longer than 78 characters, so Python's SMTP policy sent it as
  RFC 2047 encoded words, which mail clients do not read. The header is now written as one plain line.

### Added

- **Gmail API transport and mail accounts (outbound).** `MailAccount` (address, state `not_connected` / `connected` / `needs_reauth` / `error`,
  Fernet-encrypted refresh token, scopes, last error, inbox cursor); `SenderRoute.transport` (`smtp` default, `gmail_api`) and
  `SenderRoute.mail_account`. A live (or test-double sandbox) mail of a `gmail_api` route is sent with `users.messages.send` from the same RFC 822
  message the SMTP path builds. OAuth2 authorization-code flow (`access_type=offline`, `prompt=consent`, PKCE, signed one-use `state`): admin
  `accounts/` CRUD, `connect/`, `disconnect/`, `test/`, `test-send/` (test double only), public `gmail/callback/`; the signed-in address must equal the
  account's. A refused refresh token sets `needs_reauth`, notifies, and holds the route's mail (not sent by another mailbox). Replies: `poll_inbox` also
  reads connected accounts with `inbound_enabled` (`history.list`) into the same `inbound_service.ingest` as IMAP. New `FailureCode` `gmail_api`.
  Settings `COMMUNICATOR_GMAIL_*` (client id / secret / redirect URI from the environment). Test double `tests/gmail_stub.py`. Migration 0018.
- **Sending health and automatic pause (outbound Q15).** `GET health/` (per route and channel, windows 24 h and 7 d:
  sent, delivered, hard and soft bounces, opt-outs, complaints, replies, percent rates), `GET/PUT health/policy/`
  (thresholds, **off by default**: hard bounces > 2 %, opt-outs > 3 %, complaints > 0.1 % of sent, minimum sample 50,
  window 168 h), `GET health/log/`, `POST routes/<key>/pause/` and `resume/` (reason required). A live channel's
  route that breaks a threshold is paused (`SenderRoute.paused_at`; its mail stays `approved` and waits, other routes
  and the channel keep sending), a `high` notification is sent and the pause is logged (`RoutePauseLog`). Only a
  manual resume ends it; after it the auto-pause judges events newer than the resume. Migration 0017.
- **Complaint and bounce feedback loop (outbound Q30).** With `COMMUNICATOR_COMPLAINT_FEEDBACK = True` (default off)
  an ARF report (`multipart/report; report-type=feedback-report`, type `abuse` or `fraud`) is stored as
  `Reply.kind=complaint`; on a live channel the recipient is suppressed (`reason=complaint`), the sequence stops
  (`complaint`), a `high` notification goes out and `message_bounced` is sent with `hard=True, reason="complaint"`.
  A soft bounce now counts towards `COMMUNICATOR_SOFT_BOUNCE_MAX_ATTEMPTS` (default 2 = the old one retry): a mail
  is retried while its `send_attempts` are below the limit.
- **Outbound glue with leads.** `COMMUNICATOR_PRESEND_CHECK` (dotted path of `(email) -> reason | None`, empty = off) is
  called right before delivery; a reason ends the message `failed/presend_check` (migration 0016, new `FailureCode`)
  with the reason as `failure_detail`; a broken hook never blocks a send. New signal `message_bounced(sender=Message,
  message, hard=True)` after a hard DSN or an SMTP 5xx recipient refusal in a live channel (sandbox bounces say
  nothing about the real recipient).
- **Sender routes (outbound roadmap Q3, with Q8 in code).** `SenderRoute` per channel (migration 0015): From mailbox,
  CC mailbox of the same person on the other domain, display name, `daily_cap` per mailbox and an optional warm-up
  (`warmup_start_cap`, `warmup_step`, `warmup_started_on`; off by default). `Thread.sender_route` holds only the key,
  `communicate(sender_route=...)` sets it, `Channel.default_sender_route` is the fallback; no key and no default keeps
  the old behaviour. An unknown or inactive route fails the message (`failed/sender_route`, readable detail) instead
  of sending from the wrong mailbox; a sandbox never copies the real CC mailbox (`X-Original-Cc`). Admin API
  `routes/`, `PATCH channel/` `default_sender_route`, `GET policy/` `mailboxes`.
- **Approval inbox (Q4).** `GET approvals/` (all channels, filters, counts, `blocker`), `POST approvals/bulk/` (approve or
  reject, per-id result, `COMMUNICATOR_BULK_APPROVAL_MAX`), `GET approvals/log/` (`BulkApprovalLog`: who, what, which
  ids). The routes sit outside `<channel_idx>/`.
- **One-click unsubscribe (Q6).** `List-Unsubscribe` and `List-Unsubscribe-Post` (RFC 8058) plus a link in the mail while
  `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` is set; public signed-token endpoint `public/unsubscribe/<token>/` (GET page, POST
  suppresses, stops sequences, emits `optout_confirmed`). A message whose template requires a legal footer and has none
  no longer leaves (`failed/legal_footer`) and cannot be accepted (409).

- **Golden set of replies (outbound roadmap T4, slice S12).** Tests only: `tests/fixtures/reply_golden.json` (Polish and
  English samples per intent class) and `tests/fake_toolbox.py` (a deterministic fake toolbox: keyword rules, respx
  mock, drop-in client). `T4_REAL_TOOLBOX=1` runs the set against a real toolbox.
- **Next steps per reply class (outbound roadmap T4, slice S11).** `ReplyIntentAction` per channel and class (migration
  0014): `sequence` (`stop`, `pause`, `pause_until_return`), `notify`, `remind_after_days`, `needs_review`,
  `min_confidence`, `auto_suppress`; admin API `GET reply-actions/`, `PUT/DELETE reply-actions/<intent>/`. Safe
  defaults: unclassified replies behave exactly as before, `not_now` / `out_of_office` pause instead of stop,
  `unclear` and `wrong_person` are flagged `needs_review`, nothing is suppressed without a person unless
  `auto_suppress` is set for `unsubscribe`. `Reply` gains `needs_review`, `action_applied`, `resume_at`, `resumed_at`,
  `remind_at`, `reminded_at`. New beat task `process_reply_followups` (hourly) and signal `reply_followup_due`; dev
  endpoint `POST test/reply-followups/`. `GET replies/` takes `?needs_review=`.
- **Reply intent classes (outbound roadmap T4, slice S10).** A human reply is classified by the AI toolbox into
  `interested`, `not_now`, `not_interested`, `out_of_office`, `wrong_person`, `unsubscribe` or `unclear` with a
  confidence (`reply_intent_service`; migration 0013 adds `Reply.intent`, `intent_confidence`, `intent_source`,
  `intent_return_date`). Off until `COMMUNICATOR_REPLY_INTENT_MODEL` is set; with the toolbox off or failing nothing
  changes (phrase detection, a person reads the reply). `unsubscribe` takes the existing suspected opt-out path
  (sequence paused, no suppression before a human confirms). Below `COMMUNICATOR_REPLY_INTENT_MIN_CONFIDENCE` the class
  is `unclear`. `GET replies/` shows the fields and takes `?intent=`. `inbound_service.ingest` is no longer atomic as
  a whole: the toolbox call runs before the transaction of the writes.
- **Autostart sequence (outbound roadmap F2).** `Sequence.autostart` (migration 0012, one per channel): the first
  delivered message of an open thread without a sequence state starts the channel's `autostart` sequence, so
  follow-ups no longer depend on a development-only endpoint. `POST sequences/` takes `autostart`; new
  `PATCH sequences/<id>/` (`is_active`, `autostart`; a second autostart sequence answers 409).

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
