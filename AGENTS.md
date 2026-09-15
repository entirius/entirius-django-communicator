# AGENTS.md

entirius-django-communicator — channel-agnostic communication for Volkanos: templates with versions, AI drafts
and review, policy-bound sending (dry_run / sandbox / live), follow-up sequences and inbound IMAP replies.
App label `django_communicator`, table prefix `django_communicator_`.

## Quick Reference

- Python ≥ 3.11, Django ≥ 4.2, PostgreSQL, Redis, Celery + celery-once, `uv`, ruff, hatchling, MPL-2.0.
- Read first: `docs/install.md` (host) · `docs/api.md` (caller) · `docs/concept.md` (why) ·
  `docs/operations.md` (day 2, go/no-go for live) · `docs/gotchas.md` (before editing). This file is the map.

## Commands

| Command | Meaning |
|---|---|
| `make install` | `uv sync --all-extras` |
| `make test` | pytest — Postgres only, see Testing |
| `make check` / `fix` | ruff lint + format (+ canonical `.gitleaks.toml` guard) |
| in zeno: `make module-test MODULE=entirius-django-communicator` | the same suite inside the service container |

## Conventions

- English only; MPL-2.0 header on every `.py` (`insert-license`).
- Layered: API → services → models. No logic in models beyond `clean()` validation; views parse, call a
  service, serialise.
- Never rename the package, the app label or the table prefix; never edit a released migration.
- Git flow: `develop` + `master`, PRs, semver tag on `master`. Do not commit by default — the operator decides.
- **NEVER add `Co-Authored-By: Claude …` or any Claude/Anthropic attribution** to commits or PR descriptions —
  no co-author trailer, no "Generated with Claude Code" line.

## Map

```
src/django_communicator/
├── apps.py  enums.py (statuses + MESSAGE_STATUS_TRANSITIONS)  settings.py  urls.py  gdpr.py  admin.py
├── models/     channel  message_template  message_template_version  thread  message  suppression
│               send_policy  send_window  sequence  sequence_step  text_pool  thread_pool_usage
│               thread_sequence_state  mailbox_config  reply  inbound_quarantine
├── schemas/    requests.py  responses.py (Pydantic)
├── api/admin/  urls.py  views/ (review, template, suppression, sending, sequence, thread, reply, mailbox, test)
├── services/   communicate_service (communicate)  drafting_service  render_service  template_service
│               message_service (the only status writer)  review_service  preview_service
│               send_service (send_due run)  delivery_service (deliver)  policy_service  counter_service
│               clock_service  channel_service (live_allowed)  mail_builder  alert_service
│               sequence_service  sending_config_service  suppression_service
│               poll_service  inbound_service (ingest)  mail_parser  dsn_service  optout_service  inbox_service
│               anonymisation_service  draft_retry_service (transient failure retry)
├── signals/    __init__.py (message_approved, company_skipped, message_sent, sequence_finished,
│               reply_received, optout_confirmed)  leads_receivers.py (contact_anonymised)
├── tasks/      send_due  schedule_follow_ups  poll_inbox  record_objection  retry_failed_drafts
└── utils/      domains  emails  encryption  encrypted_field (copies — never cross-imported)
```

Flow: caller `communicate()` → `Message` (`review_required` | `approved` | `failed` | `suppressed`) → review
(accept / rewrite / edit / skip) → beat `send_due` → `delivery_service.deliver` (mode, policy, cap, send-once)
→ SMTP via django_email → IMAP `poll_inbox` → `inbound_service.ingest` → `Reply` + signals + alerts.

## Where things live

| Question | Answer |
|---|---|
| A setting's name, default, meaning | `settings.py`; the table in `docs/install.md` |
| Allowed status changes | `enums.MESSAGE_STATUS_TRANSITIONS`; diagram in `docs/concept.md` |
| The `communicate()` contract | `services/communicate_service.py`; `docs/concept.md` § communicate() |
| The live double gate | `channel_service.live_allowed`; `docs/concept.md` § Sending |
| Cap, spread quota, next slot | `policy_service.py`, `counter_service.py` |
| Inbound order (duplicate → DSN → thread → auto → opt-out → reply) | `inbound_service.ingest`; `docs/concept.md` § Inbound |
| Request / response shape, auth, errors | `docs/api.md`; `schemas/`; `docs/openapi.yaml` |
| Tasks, queues, alerts, what to watch, live checklist | `docs/operations.md` |
| Which test file covers what | `docs/testing.md` |
| What changed and why | `CHANGELOG.md` |

## Testing

- Postgres only (`tests/settings.py`): `DATABASE_URL` wins (zeno container), else
  `postgresql://postgres:postgres@localhost:5432/test_communicator`. Migrations run in tests.
- Toolbox mocked with `django_utils.toolbox.testing.mock_toolbox`; counter on fakeredis; mail in
  `django.core.mail.outbox`; celery-once on a file backend; `imaplib` faked; inbound fixtures in
  `tests/fixtures/mail/*.eml` (copies of the emporium fixtures — keep them in sync).
- Test names carry the edge-case ID (`test_C14_…`, `test_L17_…`); an ID absent from the suites is a gap.

## Testing end-to-end

- Covered IDs, unit (this repo): C-01…C-33 — all of the communicator edge cases — plus L-16, L-17
  (retention / erasure hooks). Per file: `docs/testing.md`.
- Covered IDs, BDD (Emporium test package, `features/communicator/`): C-01, C-02, C-06, C-07, C-08, C-09, C-29
  (`communicator_draft.feature`); C-13, C-14, C-16, C-17, C-28 (`communicator_send.feature`); C-20…C-24
  (`communicator_inbound.feature`, needs `make mail`).
- One-shot tag: `@communicator-oneshot` (C-17, daily cap) — a re-run needs a fresh `make seed`.
- Dev-only test endpoints (`ENVIRONMENT == "development"`, else 404): `test/communicate/`, `test/clock/`,
  `test/send-due/`, `test/start-sequence/`, `test/poll-now/`, `test/reset-counters/`, `test/retry-drafts/`,
  `test/toolbox-outage/` — `docs/api.md`.
- zeno: `make toolbox-check && make seed && make mail && make bdd TAGS=@communicator`; the funnel journey:
  `make e2e-funnel` (Emporium `e2e/cms/test_leads_funnel.py`).
- Guides: portal `guides/leads-end-to-end-testing.md` (modes A/B/C, funnel steps); Emporium
  `docs/e2e-leads-funnel.md` (page objects, one-shot tags).

## Gotchas

`docs/gotchas.md` — the only list. Read it before touching statuses, sending, sequences, inbound or GDPR.
