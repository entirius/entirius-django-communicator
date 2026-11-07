---
title: Admin API
description: Admin API v2 per channel — endpoints, auth, pagination, errors, development-only endpoints, OpenAPI.
---

All endpoints live under `/api/communicator/v2/admin/<channel_idx>/` (the host mounts
`django_communicator.urls`). Request and response shapes are Pydantic models in `schemas/`; the
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
| `POST review/<id>/accept/` | 200 `approved` |
| `POST review/<id>/skip/` · `skip-company/` (`{reason}`) | 200 `rejected`; `skip-company` also emits `company_skipped` |
| `POST review/<id>/rewrite/` (`{notes}`) | 201 new AI version (or a `failed` version on a toolbox error); 503 `AI_TOOLBOX_DISABLED` and no version while the toolbox is switched off |
| `POST review/<id>/edit/` (`{subject, body_text}`) | 201 new human version |
| `GET/POST templates/` · `GET/PUT templates/<id>/` · `GET templates/<id>/versions/` | editor; a content change creates a version; `audience` (upper-case code, blank = every audience) — a PUT without `audience` keeps the stored one, `""` clears it; the list is ordered by key, then audience |
| `POST templates/<id>/test-generate/` (`{context}`) | draft preview, nothing saved |
| `GET models/` | toolbox catalogue of the configured toolbox channel |
| `GET suppressions/?value=` · `POST suppressions/` · `DELETE suppressions/<id>/` | channel `email` / `domain` rows; the list includes global `email_token` rows, which are not deletable here |
| `GET/PATCH channel/` (`{mode, sandbox_mailbox}`; `live_enabled` read-only) | sending mode; `mode=live` or any `live_enabled` in a PATCH is a 400 — live is set in Django admin only |
| `GET/PUT policy/` | policy + windows (PUT replaces the windows), `timezone`, `country`, `sent_today`, `next_slot`, `mailboxes` (cap of today and deliveries per route mailbox) |
| `GET messages/?status=` | outbox (default `approved`) with `next_slot` per message |
| `POST messages/<id>/send-now/` | `scheduled_at` = channel now; mode, policy and cap still apply (C-31) |
| `GET/POST sequences/` (`autostart?`) · `PATCH sequences/<id>/` (`{is_active?, autostart?}`; a second `autostart` in a channel → 409) · `GET sequences/<id>/steps/` · `GET/POST sequences/<id>/texts/` | sequences, steps, text pool |
| `PATCH sequences/<id>/texts/<text_id>/` (`{body?, is_active?}`) · `DELETE` same path | edit or restore a pool text (future follow-ups only — sent mail keeps its body); DELETE of a text no thread used → 204, deleted; of a used one → 200 with the row, `is_active=false` (the thread history stays, no thread gets the same text twice) |
| `GET threads/?subject_ref=&state=&sort=` · `GET threads/<id>/` | the inbox list: newest created first, or `sort=activity` (latest mail, draft or reply first); `state` = `draft` (a draft waits for review) · `waiting` (a mail waits for the send beat) · `replied`; each row adds `activity_at`, `subject`, `last_text` (300 chars), `draft {id, subject}`, `waiting {id, status, scheduled_at, next_slot}`; the page carries `counts {all, draft, waiting, replied}` (ignoring `state`) — one thread with `sequence` state and `timeline` (messages + replies; `message_id` on message entries, null on replies) |
| `GET conversations/?state=` | the Inbox list: one row per `subject_ref` (a conversation) = a `threads/` row of its newest thread (newest created) plus `thread_count` and `replied` (any thread in status replied); `state` holds when any thread is in it; `draft`, `waiting` and `last_text` come from any thread (the draft to open may sit in an older one, so `draft` adds `recipient_email` of its own thread), `subject` is the newest thread's; latest activity of any thread first; `counts` count conversations (ignoring `state`) |
| `POST threads/<id>/resume-sequence/` | paused sequence runs again, thread `open` |
| `GET replies/?kind=&thread=&intent=&needs_review=` | replies, newest first; `needs_review` also on every row with `action_applied` (the plan used), `resume_at` / `resumed_at` (out of office) and `remind_at` / `reminded_at`; each row carries `intent` (`interested` · `not_now` · `not_interested` · `out_of_office` · `wrong_person` · `unsubscribe` · `unclear`, empty when not classified), `intent_confidence`, `intent_source` (`toolbox`) and, for `out_of_office`, `intent_return_date` |
| `GET reply-actions/` · `PUT/DELETE reply-actions/<intent>/` | the next step per intent class in this channel (T4): `{sequence: stop·pause·pause_until_return, notify: none·low·medium·high, remind_after_days, needs_review, min_confidence, auto_suppress}`; GET lists all seven classes with `is_default`; PUT replaces one class (`pause_until_return` only for `out_of_office`, `auto_suppress` only for `unsubscribe`, else 400); DELETE returns it to the default; applies to replies that arrive afterwards |
| `POST replies/<id>/confirm-optout/` · `dismiss-optout/` | decide a `suspected_optout` |
| `GET footers/` · `GET/PUT/DELETE footers/<language>/` (`{html}`) | HTML mail footer per ISO 639-1 language; PUT sanitises to an allowlist and needs `{{ legal }}` exactly once, as text — not in an attribute (else 400 on `html`); unknown language → 404; GET 404 when none |
| `GET/POST routes/` · `GET/PUT/DELETE routes/<key>/` | sender routes (Q3): `{key, name, from_email, cc_email, is_active, daily_cap, warmup_start_cap, warmup_step, warmup_started_on}`; mail of a thread with this key leaves from `from_email` with `cc_email` in CC; read-only `is_default`, `cap_today`, `sent_today`; 409 for an existing key, a warm-up without `daily_cap`, or deleting the channel default |
| `GET health/` | Q15: `{channel: {windows}, routes: [{route_key, from_email, is_active, paused, paused_at, pause_reason, windows}], policy}`; `windows` = `24h` / `7d`, each `{sent, delivered, hard_bounces, soft_bounces, optouts, complaints, replies, hard_bounce_rate_pct, soft_bounce_rate_pct, optout_rate_pct, complaint_rate_pct, reply_rate_pct}` (rates are percent of sent, null without sent) |
| `GET/PUT health/policy/` | Q15 auto-pause thresholds `{enabled (default false), window_hours, min_sample, max_bounce_pct, max_optout_pct, max_complaint_pct}` |
| `GET health/log/` | pause / resume log, newest first (100): `{id, route_key, action, trigger, reason, metrics, user, created_at}` |
| `POST routes/<key>/pause/` · `resume/` | body `{reason}` (3-255 chars); 409 when already paused / not paused. Routes also carry `paused`, `paused_at`, `pause_reason` |
| `PATCH channel/` `{default_sender_route}` | the route used when a thread names none (`""` clears; unknown or inactive → 409 `SENDER_ROUTE_UNKNOWN`) |
| `GET approvals/` · `POST approvals/bulk/` · `GET approvals/log/` | **not under `<channel_idx>/`**: `/api/communicator/v2/admin/approvals/…`. The approval inbox (Q4): every `review_required` outbound draft of every channel, oldest first, filters `channel`, `sender_route` (effective), `template`, `q`, `min_age_hours`; rows carry route, From/CC, company, template, age and `blocker`; `counts` per channel and route. Bulk `{action: approve·reject, ids, reason}` handles each id alone → `{id, action, done, failed{id: why}}`, more than `COMMUNICATOR_BULK_APPROVAL_MAX` ids → 400; every batch is logged with the user (`log/`) |
| `GET/PUT mailbox/` | IMAP config; `imap_password` is write-only (`has_password` on read), omitted on PUT = kept; GET 404 when none |

List endpoints `review/`, `messages/`, `threads/`, `conversations/` and `replies/` are paginated (`page`, `page_size`
≤ 100, default 20) with `count`, `next`, `previous`, `results`.

## Errors

| Status | Cause |
|---|---|
| 400 | `PATCH channel/` with `mode=live` or `live_enabled` (field named in `details`); Pydantic validation (v2 error shape via `raise_pydantic_as_drf`); template model validation; unrenderable `test-generate` context; invalid suppression value |
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
| `REVIEW_REFUSED` | rewrite of a message that is not an AI draft |
| `NOT_WAITING` | `send-now/` of a message that is not `approved` / `scheduled` |
| `CHANNEL_MODE_INVALID` | `PATCH channel/` `sandbox` without a mailbox (C-30) |
| `CHANNEL_CONFIG_INVALID` | the stored channel country or timezone cannot drive a policy |
| `DUPLICATE_SUPPRESSION` · `DUPLICATE_SEQUENCE` | the suppression value or sequence key already exists |
| `OPTOUT_STATE` | opt-out action on anything but an undecided `suspected_optout`; `resume-sequence/` while an opt-out is undecided or the sequence is not paused |
| `SENDER_ROUTE_UNKNOWN` · `ROUTE_EXISTS` · `ROUTE_CONFIG_INVALID` · `ROUTE_IS_DEFAULT` | default route that does not exist or is inactive; a second route with the same key; a warm-up without `daily_cap`; deleting the channel default route |
| `REVIEW_REFUSED` (accept) | the draft has no legal footer although its template requires one, or names an unusable sender route (Q3, Q6) |
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
| `POST test/reply-followups/` | `process_reply_followups` in-process under its celery-once lock (409 while held) → `{resumed, reminded}`; move the clock with `test/clock/` first |
| `POST test/toolbox-outage/` (`{down}`) | turn the `django_utils.toolbox.outage` switch on / off; 404 where the switch is not allowed (no `DEBUG` and no `AI_TOOLBOX_TEST_SWITCH`, or production) |

## Mail accounts and the Gmail API transport

Under `/api/communicator/v2/admin/<channel>/` (staff JWT, area `communicator.settings`). Error codes are upper case in `error`.

| Call | What |
|---|---|
| `GET/POST accounts/` | list / add `{email, inbound_enabled}` (not connected yet; 409 `ACCOUNT_EXISTS`) |
| `GET/PATCH/DELETE accounts/<id>/` | one account (`state`, `scopes`, `can_send`, `can_receive`, `last_error`, `last_ok_at`, `routes`; never a token); `PATCH {inbound_enabled}`; DELETE revokes the token, 409 `ACCOUNT_IN_USE` while a route uses it |
| `POST accounts/<id>/connect/` | `{authorization_url, expires_in}`; open the URL in a browser. 409 `GMAIL_NOT_CONFIGURED` |
| `GET /api/communicator/v2/public/gmail/callback/` | Google's redirect target (no login, signed one-use `state`): stores the encrypted refresh token, then shows a page or redirects to `COMMUNICATOR_GMAIL_RETURN_URL?gmail=<result>`; results `connected`, `account_mismatch`, `scope_missing`, `no_refresh_token`, `state_invalid`, `exchange_failed`, `consent_denied` |
| `POST accounts/<id>/disconnect/` | revoke at Google, forget the token, state `not_connected` |
| `POST accounts/<id>/test/` | always 200 `{ok, checks, error, account}`; refuses nothing, updates the state (`needs_reauth` for a refused token) |
| `POST accounts/<id>/test-send/` | `{to}`; only while the API base is not Google's (the test double), else 409 `TEST_SEND_NEEDS_DOUBLE` |
| `routes/` | new fields `transport` (`smtp` or `gmail_api`), `mail_account_id` (required for `gmail_api`; the account address must equal `from_email`, else 409 `ROUTE_CONFIG_INVALID`); responses add `mail_account_state` |

## Public unsubscribe (no login)

`GET /api/communicator/v2/public/unsubscribe/<token>/` shows a confirmation page and changes nothing (link scanners
open URLs). `POST` on the same URL (the mail client's RFC 8058 one-click request, body `List-Unsubscribe=One-Click`, or
the page's button) suppresses the address on the channel, stops its running sequences and emits `optout_confirmed`;
idempotent. The token is `django.core.signing` over channel and address, never expires. A bad token: GET 404, POST 400.
Headers and link appear only while `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` is set. The route is not in `openapi.yaml`.

## OpenAPI

`docs/openapi.yaml` is generated from a host (`manage.py spectacular`) and filtered to
`/api/communicator/` with the referenced components; `tests/test_openapi.py` validates the module's own
schema. Regenerate it whenever a request or response model changes.
