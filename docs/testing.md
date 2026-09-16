---
title: Test suite map
description: Which test file covers what, the edge-case IDs per file, and the end-to-end layers outside this repo.
---

Postgres only (`tests/settings.py`): `DATABASE_URL`, else
`postgresql://postgres:postgres@localhost:5432/test_communicator`. The toolbox is mocked with
`django_utils.toolbox.testing.mock_toolbox` (respx), the daily counter runs on fakeredis, mail lands in
`django.core.mail.outbox`, celery-once uses a file backend, `imaplib` is faked. Test names carry the
edge-case ID (`test_C14_…`).

| File | Covers | IDs |
|---|---|---|
| `test_communicate.py` | suppression, template fallback, footer, render, static vs AI, toolbox failure codes and notifications | C-01…C-06, C-10…C-12, C-33 |
| `test_draft_retry.py` | beat retry of transiently failed drafts: transient vs permanent, attempts cap, unreachable toolbox no-op, claim, skips, no call inside a transaction, dev retry and outage endpoints | FIX-16 items 2, 3 |
| `test_review.py` | accept, rewrite (limit, failures count), edit, skip, template versions | C-07…C-09, C-29 |
| `test_sending.py` | modes, live gate, send-once claim, stale `sending`, overlapping runs under the cap, SMTP 4xx/5xx and sender refusal, mail structure, send now, soft-bounce retry, dev endpoints, the single `deliver(` caller | C-13…C-16, C-18, C-19, C-31, C-32 |
| `test_policy.py` | windows, holidays, next slot, cap per channel day and spread quota, `CHANNEL_CONFIG_INVALID`, sandbox without mailbox, clock override only in development | C-16, C-17, C-30 |
| `test_sequences.py` | follow-ups, pool exhaustion, step compare-and-set, References | C-27, C-28 |
| `test_inbound.py` | poll cursor, quarantine, UIDVALIDITY, thread matching, autoresponders, opt-out, DSN, idempotency — on `tests/fixtures/mail/*.eml` (copies of the emporium fixtures) | C-20…C-26 |
| `test_gdpr.py` | `contact_anonymised` receiver, export / erase, global token suppression, token parity with django_leads (skipped without it) | L-16, L-17 |
| `test_admin_api.py` | review / template / suppression endpoints, 401/403/404/409, `test/communicate/` | — |
| `test_admin.py` | Django admin pages render, admin save versions, read-only messages | — |
| `test_openapi.py` | the module schema validates | — |
| `test_domains.py`, `test_smoke.py` | registrable domain rule; app installed | — |

## End-to-end layers (outside this repo)

| Layer | Where | IDs |
|---|---|---|
| BDD | Emporium test package `features/communicator/communicator_draft.feature` | C-01, C-02, C-06, C-07, C-08, C-09, C-29 |
| BDD | `features/communicator/communicator_send.feature` (C-17 is `@communicator-oneshot`) | C-13, C-14, C-16, C-17, C-28 |
| BDD | `features/communicator/communicator_inbound.feature` (needs the GreenMail sandbox) | C-20…C-24 |
| e2e (Playwright) | Emporium `e2e/cms/test_leads_funnel.py` — the leads funnel through the CMS | review, send, reply as one journey |

In the zeno harness: `make module-test MODULE=entirius-django-communicator` runs this suite in the
service container; `make toolbox-check && make seed && make mail && make bdd TAGS=@communicator` runs the
BDD layer. `@communicator-oneshot` scenarios need a fresh seed.
