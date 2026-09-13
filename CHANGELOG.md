# Changelog

## 0.1.0 (unreleased)

- Scaffold from the Entirius module template.
- Templates with versions, `communicate()` (suppression, language fallback, legal footer, render, AI drafts over
  the toolbox with failure codes and notifications), review queue and admin API v2, suppressions, signals.
- Sending: channel modes (dry_run / sandbox / live, double-gated), send policy with hour windows, holidays and a
  Redis daily cap, `send_due` beat under celery-once, multipart mail with Message-ID and References on the
  django_email channel connection, SMTP 4xx retry / 5xx failure + suppression, follow-up sequences over text pools,
  admin API for channel mode, policy, outbox, send now and sequences, development clock and beat endpoints.
