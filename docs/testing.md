---
title: Test suite map
description: Which test file covers what, the edge-case IDs per file, and the end-to-end layers outside this repo.
---

Postgres only (`tests/settings.py`; on sqlite 9 older tests fail — inbox conversations, deferred-FK — that is why): `DATABASE_URL`, else
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
| `test_sending.py` | modes, live gate, send-once claim, stale `sending`, overlapping runs under the cap, SMTP 4xx/5xx and sender refusal, mail structure, send now, soft-bounce retry, dev endpoints, the single `deliver(` caller | C-13…C-16, C-18, C-19, C-31, C-32 |
| `test_policy.py` | windows, holidays, next slot, cap per channel day and spread quota, `CHANNEL_CONFIG_INVALID`, sandbox without mailbox, clock override only in development | C-16, C-17, C-30 |
| `test_sequences.py` | follow-ups, pool exhaustion, step compare-and-set, References | C-27, C-28 |
| `test_text_pool.py` | pool text PATCH / DELETE (delete vs deactivate), 404 across sequence and channel, inactive texts never picked | — |
| `test_footer.py` | HTML footer API, `{{ legal }}` rule, sanitising, language cascade, text part, sent `footer_html` snapshot, GDPR export / erase | — |
| `test_reply_intent.py` | T4 intent classes over the FAKE toolbox (`tests/fake_toolbox.py`: keyword rules for pl/en, a respx mock, a real local HTTP server, a drop-in client): storage, threshold, unsubscribe = suspected opt-out, phrase disagreement, out-of-office date rules, toolbox off / unconfigured / failing / unusable output, autoresponder and DSN never classified, request shape (reply text only as user data), the `intent` API filter | — |
| `test_reply_actions.py` | T4 next steps: defaults, `reply-actions/` API (validation, per channel, staff only), each class applied (sequence, alert, review flag, reminder, resume date), unclassified = pre-T4, stored mapping, per-class threshold, `unsubscribe` + `auto_suppress`, reminders and resume (once, superseded, suppressed, closed, stopped, failing row), dev endpoint and task | — |
| `test_reply_golden.py` | T4 golden set (`tests/fixtures/reply_golden.json`, 51 Polish and English replies over all seven classes plus edge cases: quotes, signatures, injection text, empty body; a copy lives in leads): per reply the fake toolbox class and the whole default mapping end to end; opt-in `T4_REAL_TOOLBOX=1` (+ `T4_TOOLBOX_URL`, `_KEY`, `_CHANNEL`, `_MODEL`) runs the same set through a real toolbox and reports the misses (skipped otherwise) | — |
| `test_inbound.py` | poll cursor, quarantine, UIDVALIDITY, thread matching, autoresponders, opt-out, DSN, idempotency — on `tests/fixtures/mail/*.eml` (copies of the emporium fixtures) | C-20…C-26 |
| `test_gdpr.py` | `contact_anonymised` receiver, export / erase, global token suppression, token parity with django_leads (skipped without it) | — |
| `test_inbox_list.py`, `test_inbox_conversations.py` | the inbox lists: `threads/` (state filter, counts, activity order, row fields) and `conversations/` (one row per `subject_ref`, state from any thread, counts, order, paging), query-count bounds | — |
| `test_sender_routes.py` | Q3/Q8: route From + CC in live, default route, no key, sandbox keeps CC out, unknown / inactive route fails readable and gives the cap back, thread keeps its route, routes CRUD + channel default, per-mailbox cap (shared From, deferral, reservation back), warm-up schedule, policy `mailboxes`, one reply stored once whichever mailbox delivered it | — |
| `test_approvals.py` | Q4: cross-channel list, filters, only `review_required`, query bound, bulk approve / reject with per-id failures, log with user, size limit, blockers, staff only | — |
| `test_unsubscribe.py` | Q6: headers and link on / off, public POST / GET / forged tokens, escaping, suppression + owner signal, later send suppressed, legal footer at delivery and at accept | — |
| `test_health.py` | Q15: counts and rates per route and channel, 24 h / 7 d, default-route mapping, API and staff-only access, policy API; auto-pause (off, minimum sample, threshold edge, only the bad route, log, one high notification, opt-out and complaint triggers, sandbox ignored), paused mail waits in `send_due`, manual pause / resume with reason, no re-pause on old events; Q30: ARF complaint (off, live, sandbox, not-spam, forged recipient), hard-bounce signal, soft-bounce attempt limit | — |
| `test_admin_api.py` | review / template / suppression endpoints, 401/403/404/409, `test/communicate/` | — |
| `test_v2_error_exposure.py` | admin refusals answer `client_message`, never `str(exc)`; a lower-layer exception is a 500 `INTERNAL_ERROR` with no text of it | — |
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

Dev-only test endpoints (`ENVIRONMENT == "development"`, else 404): `test/communicate/`, `test/clock/`,
`test/send-due/`, `test/start-sequence/`, `test/poll-now/`, `test/reset-counters/`, `test/retry-drafts/`,
`test/toolbox-outage/` — `api.md`.

In the entirius-zeno harness: `make module-test MODULE=entirius-django-communicator` runs this suite in the
service container; `make toolbox-check && make seed && make mail && make bdd TAGS=@communicator` runs the
BDD layer (`communicator_inbound.feature` needs `make mail`); `make e2e-funnel` runs the funnel journey.
`@communicator-oneshot` scenarios (C-17, the daily cap) need a fresh `make seed`.

Guides: the Entirius documentation at https://docs.entirius.com/ and Emporium `docs/e2e-leads-funnel.md`
(page objects, one-shot tags).

## Gmail API double

`tests/gmail_stub.py` is a standard-library HTTP server that stands in for Google's OAuth endpoints and the Gmail API (consent redirect with PKCE
check, token / refresh / revoke, `getProfile`, `messages.send`, `messages.list/get`, `history.list`) plus `/__stub/...` control endpoints (inject an
inbox message, read what was sent, revoke, make sends fail, expire the history id). The `gmail` fixture in `tests/conftest.py` starts it and points
`COMMUNICATOR_GMAIL_*` at it; `tests/settings.py` names hosts that do not resolve, so a test without the fixture cannot reach Google. Standalone:
`python tests/gmail_stub.py --port 9099`.
