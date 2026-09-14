# Changelog

## 0.1.0 (unreleased)

- Scaffold from the Entirius module template.
- Templates with versions, `communicate()` (suppression, language fallback, legal footer, render, AI drafts over
  the toolbox with failure codes and notifications), review queue and admin API v2, suppressions, signals.
- Sending: channel modes (dry_run / sandbox / live, double-gated), send policy with hour windows, holidays and a
  Redis daily cap, `send_due` beat under celery-once, multipart mail with Message-ID and References on the
  django_email channel connection, SMTP 4xx retry / 5xx failure + suppression, follow-up sequences over text pools,
  admin API for channel mode, policy, outbox, send now and sequences, development clock and beat endpoints.
- Retention and GDPR: `contact_anonymised` receiver (soft django_leads dependency), `gdpr.py` export/erase hooks,
  anonymised token addresses always suppressed.
- GDPR fixes: erased and retention-anonymised addresses suppressed on every channel by a global `email_token`
  suppression (no plain address kept); export and erasure scoped to the subject's own replies; export complete
  (HTML body, footer, prompt, render context), erased subjects scrubbed; `GET suppressions/?value=`.
- Inbound: IMAP poll beat (`poll_inbox`, read-only, UID cursor) into `Reply` rows — header/sender thread matching,
  autoresponders, suspected opt-outs with confirm/dismiss (suppression, `optout_confirmed`, agreements objection),
  DSN hard/soft bounces, idempotent by inbound Message-ID; `MailboxConfig` with an encrypted password; admin API for
  threads with timeline, replies, opt-out actions and the mailbox, development `test/poll-now/`.
