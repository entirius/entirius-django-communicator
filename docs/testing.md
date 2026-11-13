---
title: Test suite map
description: Which test file covers what, the edge-case IDs per file, and the end-to-end layers outside this repo.
---

Postgres only (`tests/settings.py`): `DATABASE_URL`, else
`postgresql://postgres:postgres@localhost:5432/test_communicator`. The toolbox is mocked with
`django_utils.toolbox.testing.mock_toolbox` (respx), the daily counter runs on fakeredis, mail lands in
`django.core.mail.outbox`, celery-once uses a file backend, `imaplib` is faked. Migrations run in tests.
Inbound fixtures live in `tests/fixtures/mail/*.eml` (copies of the emporium fixtures — keep them in sync).
Test names carry the edge-case ID (`test_C14_…`); an ID absent from the suites is a gap. This suite covers
C-01…C-33, all of the communicator edge cases.

| File | Covers | IDs |
|---|---|---|
| `test_communicate.py` | suppression, template fallback, footer, render, static vs AI, toolbox failure codes and notifications | C-01…C-06, C-10…C-12, C-33 |
| `test_draft_retry.py` | beat retry of transiently failed drafts: transient vs permanent, attempts cap, unreachable toolbox no-op, claim, skips, no call inside a transaction, dev retry and outage endpoints | — |
| `test_toolbox_disabled.py` | toolbox switched off: AI `communicate()` and rewrite refused with nothing written, static and suppressed paths unchanged, 503 `AI_TOOLBOX_DISABLED` on the admin API, retry no-op, due AI follow-up waits; unconfigured paths unchanged | — |
| `test_review.py` | accept, rewrite (limit, failures count), edit, skip, template versions | C-07…C-09, C-29 |
| `test_preflight.py` | legal footer at accept (409) and at delivery (`failed/legal_footer`), template-less messages | — |
| `test_approvals.py` | approval inbox across channels: filters, counts, `blocker`, bulk approve / reject each id on its own, limit 400, log, staff only | — |
| `test_unsubscribe.py` | token, RFC 8058 headers on the wire (unfolded, other headers unchanged), links by body language, public GET / POST, idempotency, 400 on a bad token, throttle 429 and its fallback rate | — |
| `test_sending.py` | modes, live gate, send-once claim, stale `sending`, overlapping runs under the cap, SMTP 4xx/5xx and sender refusal, mail structure, send now, soft-bounce retry, dev endpoints, the single `deliver(` caller | C-13…C-16, C-18, C-19, C-31, C-32 |
| `test_policy.py` | windows, holidays, next slot, cap per channel day and spread quota, `CHANNEL_CONFIG_INVALID`, sandbox without mailbox, clock override only in development | C-16, C-17, C-30 |
| `test_sequences.py` | follow-ups, pool exhaustion, step compare-and-set, References, autostart (never for a template-less message, never twice in a thread, one per channel via the API) | C-27, C-28 |
| `test_text_pool.py` | pool text PATCH / DELETE (delete vs deactivate), 404 across sequence and channel, inactive texts never picked | — |
| `test_footer.py` | HTML footer API, `{{ legal }}` rule, sanitising, language cascade, text part, sent `footer_html` snapshot, GDPR export / erase | — |
| `test_inbound.py` | poll cursor per mailbox (two mailboxes, each failure announced), quarantine, UIDVALIDITY, thread matching, autoresponders, opt-out (folded phrases, pl and en refusals), DSN, ARF complaints, `message_bounced`, idempotency — on `tests/fixtures/mail/*.eml` (copies of the emporium fixtures) | C-20…C-26 |
| `test_reply_classes.py` | reply classes over a fake toolbox (`tests/fake_toolbox.py`): request shape (reply only as data, closed schema, tags), every failure path (no / inactive profile, toolbox off, transport, 5xx, output outside the schema) back to the unclassified behaviour, threshold and return date, toolbox call outside a transaction, the opt-out / class matrix, every row of the default plan table, stored plans, `reply_received` receivers, out-of-office resume (due, superseded, suppressed, closed), profile / plan / reply APIs, dev `test/ingest-reply/` per class and outside development, the shipped default profile | — |
| `test_reply_golden.py` | the golden set (`tests/fixtures/reply_golden.json`, Polish and English per class) through the fake toolbox, end to end with the default plans; not a measure of a real model | — |
| `test_mailboxes.py` | mailbox resolution (user → default → none), `Thread.mailbox` set once, CRUD API, write-only encrypted passwords, an undecryptable password never overwritten, states, login test, approval filter / counts / blocker | — |
| `test_mailbox_sending.py` | From / CC / login per mailbox, sandbox CC header, channel without mailbox, unusable or refused login holds one message and alerts once, pause holds one mailbox, per-mailbox cap, warm-up arithmetic, release on failure, soft-bounce retry | — |
| `test_health.py` | counts per mailbox, auto-pause live only and above the sample, manual pause / resume and log, `health/` with `sent_today` / `cap_today`, policy API, no password in responses, logs or notifications | — |
| `test_mailbox_migration.py` | `MailboxConfig` → `Mailbox` forward and backward with one old row and with none, ciphertext kept | — |
| `test_suppression_scope.py` | `COMMUNICATOR_SUPPRESSION_SCOPE`: channel scope isolates two channels, install scope blocks in both, rows keep working after a switch; sources of every writer; global-kind constraint; `communicator_suppressions --to-install` dry run and apply; list filters `scope` / `source` / `search`; global delete, token row kept; health opt-outs from global rows; the 0019 data migration | — |
| `test_address_status.py` | every DNS verdict, syntax, disposable (stub resolver, no network); hard bounce final; stale re-check and the beat; `can_mail` reasons and query count; live SMTP and DSN hard bounces, sandbox none; delivery and `communicate()` refusals; verification on and off; `addresses/status/`; GDPR erase and anonymisation drop the row | — |
| `test_gdpr.py` | `contact_anonymised` receiver, export / erase, global token suppression, token parity with django_leads (skipped without it) | — |
| `test_inbox_list.py`, `test_inbox_conversations.py` | the inbox lists: `threads/` (state filter, counts, activity order, row fields) and `conversations/` (one row per `subject_ref`, state from any thread, counts, order, paging), query-count bounds | — |
| `test_admin_api.py` | review / template / suppression endpoints, 401/403/404/409, `test/communicate/` | — |
| `test_v2_error_exposure.py` | admin refusals answer `client_message`, never `str(exc)`; a lower-layer exception is a 500 `INTERNAL_ERROR` with no text of it | — |
| `test_admin.py` | Django admin pages render (address status included), admin save versions, read-only messages | — |
| `test_openapi.py` | the module schema validates | — |
| `test_domains.py`, `test_smoke.py` | registrable domain rule; app installed | — |

## End-to-end layers (outside this repo)

| Layer | Where | IDs |
|---|---|---|
| BDD | Emporium test package `features/communicator/communicator_draft.feature` | C-01, C-02, C-06, C-07, C-08, C-09, C-29 |
| BDD | `features/communicator/communicator_send.feature` (C-17 is `@communicator-oneshot`) | C-13, C-14, C-16, C-17, C-28 |
| BDD | `features/communicator/communicator_inbound.feature` (needs the GreenMail sandbox) | C-20…C-24 |
| e2e (Playwright) | Emporium `e2e/cms/test_leads_funnel.py` — the leads funnel through the CMS | review, send, reply as one journey |

Dev-only test endpoints (`ENVIRONMENT == "development"`, else 404): `test/communicate/`, `test/clock/`,
`test/send-due/`, `test/start-sequence/`, `test/poll-now/`, `test/reset-counters/`, `test/retry-drafts/`,
`test/toolbox-outage/` — `api.md`.

In the entirius-zeno harness: `make module-test MODULE=entirius-django-communicator` runs this suite in the
service container; `make toolbox-check && make seed && make mail && make bdd TAGS=@communicator` runs the
BDD layer (`communicator_inbound.feature` needs `make mail`); `make e2e-funnel` runs the funnel journey.
`@communicator-oneshot` scenarios (C-17, the daily cap) need a fresh `make seed`.

Guides: the Entirius documentation at https://docs.entirius.com/ and Emporium `docs/e2e-leads-funnel.md`
(page objects, one-shot tags).
