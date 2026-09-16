# Changelog

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
