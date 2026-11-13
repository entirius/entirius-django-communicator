---
title: Admin API
description: Admin API v2 per channel, the approval inbox, the public unsubscribe page — endpoints, auth, pagination, errors, development-only endpoints, OpenAPI.
---

Admin endpoints live under `/api/communicator/v2/admin/<channel_idx>/`, the approval inbox under
`/api/communicator/v2/admin/approvals/` (declared before the channel include, so it wins over a channel idx
`approvals` — the system check `communicator.reserved_idx` refuses such a channel) and the one public route under
`/api/communicator/v2/public/` (the host mounts `django_communicator.urls`). Request and response shapes are Pydantic models in `schemas/`; the
generated document is `docs/openapi.yaml`. Views are thin: parse, call a service, serialise.

## Auth

| | |
|---|---|
| Authentication | `JWTAuthentication` — declared on every view, never inherited from host defaults |
| Permission | `IsAdminUser` (staff) |
| Channel | an unknown `<channel_idx>` → 404; an object of another channel → 404 |

## Endpoints

| Endpoint | Meaning |
|---|---|
| `GET review/?status=&page=&page_size=` | messages by status (default `review_required`), oldest first |
| `GET review/next/` | oldest `review_required`; 404 when the queue is empty |
| `GET review/<id>/` | one message of the channel, any status (`MessageDetailResponse`); 404 for another channel's message |
| `POST review/<id>/accept/` | 200 `approved`; 409 `REVIEW_REFUSED` when the preflight finds a problem (a legal footer is required and missing) |
| `POST review/<id>/skip/` · `skip-company/` (`{reason}`) | 200 `rejected`; `skip-company` also emits `company_skipped` |
| `POST review/<id>/rewrite/` (`{notes}`) | 201 new AI version (or a `failed` version on a toolbox error); 503 `AI_TOOLBOX_DISABLED` and no version while the toolbox is switched off |
| `POST review/<id>/edit/` (`{subject, body_text}`) | 201 new human version |
| `GET/POST templates/` · `GET/PUT templates/<id>/` · `GET templates/<id>/versions/` | editor; a content change creates a version; `audience` (upper-case code, blank = every audience) — a PUT without `audience` keeps the stored one, `""` clears it; the list is ordered by key, then audience |
| `POST templates/<id>/test-generate/` (`{context}`) | draft preview, nothing saved |
| `GET models/` | toolbox catalogue of the configured toolbox channel |
| `GET suppressions/?value=` · `POST suppressions/` · `DELETE suppressions/<id>/` | channel `email` / `domain` rows; the list includes global `email_token` rows, which are not deletable here |
| `GET/PATCH channel/` (`{mode, sandbox_mailbox}`; `live_enabled` read-only) | sending mode; `mode=live` or any `live_enabled` in a PATCH is a 400 — live is set in Django admin only |
| `GET/PUT policy/` | policy + windows (PUT replaces the windows), `timezone`, `country`, `sent_today`, `next_slot` |
| `GET messages/?status=` | outbox (default `approved`) with `next_slot` per message |
| `POST messages/<id>/send-now/` | `scheduled_at` = channel now; mode, policy and cap still apply (C-31) |
| `GET/POST sequences/` · `GET sequences/<id>/steps/` · `GET/POST sequences/<id>/texts/` | sequences, steps, text pool; `autostart` on POST (default false) |
| `PATCH sequences/<id>/` (`{is_active?, autostart?}`) | switch a sequence on or off, or make it the channel's autostart sequence; a second autostart sequence of the channel → 409 `DUPLICATE_AUTOSTART` (also on POST) |
| `PATCH sequences/<id>/texts/<text_id>/` (`{body?, is_active?}`) · `DELETE` same path | edit or restore a pool text (future follow-ups only — sent mail keeps its body); DELETE of a text no thread used → 204, deleted; of a used one → 200 with the row, `is_active=false` (the thread history stays, no thread gets the same text twice) |
| `GET threads/?subject_ref=&state=&sort=` · `GET threads/<id>/` | the inbox list: newest created first, or `sort=activity` (latest mail, draft or reply first); `state` = `draft` (a draft waits for review) · `waiting` (a mail waits for the send beat) · `replied`; each row adds `activity_at`, `subject`, `last_text` (300 chars), `draft {id, subject}`, `waiting {id, status, scheduled_at, next_slot}`; the page carries `counts {all, draft, waiting, replied}` (ignoring `state`) — one thread with `sequence` state and `timeline` (messages + replies; `message_id` on message entries, null on replies) |
| `GET conversations/?state=` | the Inbox list: one row per `subject_ref` (a conversation) = a `threads/` row of its newest thread (newest created) plus `thread_count` and `replied` (any thread in status replied); `state` holds when any thread is in it; `draft`, `waiting` and `last_text` come from any thread (the draft to open may sit in an older one, so `draft` adds `recipient_email` of its own thread), `subject` is the newest thread's; latest activity of any thread first; `counts` count conversations (ignoring `state`) |
| `POST threads/<id>/resume-sequence/` | paused sequence runs again, thread `open` |
| `GET replies/?kind=&thread=` | replies, newest first |
| `POST replies/<id>/confirm-optout/` · `dismiss-optout/` | decide a `suspected_optout` |
| `GET footers/` · `GET/PUT/DELETE footers/<language>/` (`{html}`) | HTML mail footer per ISO 639-1 language; PUT sanitises to an allowlist and needs `{{ legal }}` exactly once, as text — not in an attribute (else 400 on `html`); unknown language → 404; GET 404 when none |
| `GET/PUT mailbox/` | IMAP config; `imap_password` is write-only (`has_password` on read), omitted on PUT = kept; GET 404 when none |

## Approval inbox (all channels)

Under `/api/communicator/v2/admin/approvals/`, area `communicator.review`, same auth as above.

| Endpoint | Meaning |
|---|---|
| `GET approvals/?channel=&template_key=&search=&min_age_hours=` | outbound `review_required` messages of every channel, oldest first; `search` matches the subject reference, the recipient address or name and the subject; each row carries `blocker` (the preflight detail, null when it may be approved); `counts {total, by_channel}` cover the whole filtered set |
| `POST approvals/bulk/` (`{action: approve \| reject, ids, reason}`) | each id handled on its own through `review_service.accept` / `skip`; answer `{id, action, done, failed: {id: reason}}`; more than `COMMUNICATOR_BULK_APPROVAL_MAX` (100) ids → 400 and nothing changes; every call writes a `BulkApprovalLog` (user, action, reason, ids) |
| `GET approvals/log/` | the bulk log, newest first |

## Public unsubscribe

`/api/communicator/v2/public/unsubscribe/<token>/`: no authentication (the signed token is the credential), no CSRF,
throttled per client address (scope `communicator_unsubscribe`, fallback `60/min` when
`DEFAULT_THROTTLE_RATES` has no valid rate; 429 above it). Not an admin route, so the access gate lets it through
without an area.

| Method | Meaning |
|---|---|
| `GET` | HTML confirmation page with a button; changes nothing (a link scanner unsubscribes nobody) |
| `POST` | one-click (RFC 8058, body `List-Unsubscribe=One-Click`) or the page button: suppresses the address on the channel (reason `unsubscribe link`), stops its sequences, sends `optout_confirmed` per `subject_ref`, records the agreements objection; a second POST changes nothing |
| invalid token | 400, HTML page with one generic message, for GET and POST |

The page speaks Polish for a channel whose default language is `pl`, English otherwise.

List endpoints `review/`, `messages/`, `threads/`, `conversations/`, `replies/`, `approvals/` and `approvals/log/` are paginated (`page`, `page_size`
≤ 100, default 20) with `count`, `next`, `previous`, `results`.

## Errors

| Status | Cause |
|---|---|
| 400 | `approvals/bulk/` over the limit; `PATCH channel/` with `mode=live` or `live_enabled` (field named in `details`); Pydantic validation (v2 error shape via `raise_pydantic_as_drf`); template model validation; unrenderable `test-generate` context; invalid suppression value |
| 401 / 403 | no or invalid JWT / not staff |
| 404 | unknown channel or object; empty review queue; no policy (`GET policy/`); no mailbox (`GET mailbox/`); no footer or unknown language (`footers/<language>/`) |
| 409 | a conflict; `error` names its kind — see the table below |
| 402 / 403 / 502 / 503 / 504 | `test-generate/` and `models/` only: toolbox budget, `MODEL_NOT_ALLOWED`, provider error or invalid draft output, toolbox not configured, timeout (`django_utils.toolbox.views.handle_toolbox_error`) |
| 503 `AI_TOOLBOX_DISABLED` | `review/<id>/rewrite/`, `test/communicate/` (AI template), `test-generate/` and `models/` while `AI_TOOLBOX_ENABLED` is `False`; body `{error, message}`, nothing is written |
| 500 `INTERNAL_ERROR` | anything that is not a refusal written by the module; body carries a `debug_id` and no exception text, the traceback is logged under the same id |

Every 409 uses the v2 error shape; `error` tells the kinds apart, `message` explains:

| `error` | When |
|---|---|
| `ALREADY_REVIEWED` | a review action (accept, skip, skip-company, rewrite, edit) on a message no longer waiting for review |
| `REVIEW_REFUSED` | rewrite of a message that is not an AI draft; accept of a message the preflight refuses (legal footer) |
| `NOT_WAITING` | `send-now/` of a message that is not `approved` / `scheduled` |
| `CHANNEL_MODE_INVALID` | `PATCH channel/` `sandbox` without a mailbox (C-30) |
| `CHANNEL_CONFIG_INVALID` | the stored channel country or timezone cannot drive a policy |
| `DUPLICATE_SUPPRESSION` · `DUPLICATE_SEQUENCE` | the suppression value or sequence key already exists |
| `DUPLICATE_AUTOSTART` | a second autostart sequence in the channel |
| `OPTOUT_STATE` | opt-out action on anything but an undecided `suspected_optout`; `resume-sequence/` while an opt-out is undecided or the sequence is not paused |
| `ALREADY_RUNNING` | development `test/send-due/`, `test/poll-now/` or `test/retry-drafts/` while the beat holds the lock |

`communicate()` failures on the review path never surface as HTTP errors — they are `failed` messages.

## Development-only endpoints

Mounted only when `ENVIRONMENT == "development"` at URL import, and each view answers 404 otherwise.
They exist for BDD and e2e and are not part of `openapi.yaml`.

| Endpoint | Does |
|---|---|
| `POST test/communicate/` | `communicate()` with a template key, recipient, context, `subject_ref`; missing footer → 400 |
| `POST test/clock/` (`{iso_datetime}` or null) | freeze / clear the channel clock |
| `POST test/send-due/` | `schedule_follow_ups` then `send_due` in-process under the beat's celery-once lock (409 while held) |
| `POST test/start-sequence/` (`{thread_id, sequence_key}`) | start a sequence in a thread |
| `POST test/poll-now/` | poll this channel's mailbox under the `poll_inbox` lock (409 while held) |
| `POST test/reset-counters/` (`{days}`) | clear the daily send counters |
| `POST test/retry-drafts/` | `retry_failed_drafts` in-process under its celery-once lock (409 while held) → `{recovered, failed}` |
| `POST test/toolbox-outage/` (`{down}`) | turn the `django_utils.toolbox.outage` switch on / off; 404 where the switch is not allowed (no `DEBUG` and no `AI_TOOLBOX_TEST_SWITCH`, or production) |

## OpenAPI

`docs/openapi.yaml` is generated from a host (`manage.py spectacular`) and filtered to
`/api/communicator/` with the referenced components; `tests/test_openapi.py` validates the module's own
schema. Regenerate it whenever a request or response model changes.
