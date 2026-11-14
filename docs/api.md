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
| `POST review/<id>/accept/` | 200 `approved`; 409 `REVIEW_REFUSED` when the preflight finds a problem (a legal footer is required and missing, the mailbox cannot send, the recipient may not be mailed — the sentence is the `message`) |
| `POST review/<id>/skip/` · `skip-company/` (`{reason}`) | 200 `rejected`; `skip-company` also emits `company_skipped` |
| `POST review/<id>/rewrite/` (`{notes}`) | 201 new AI version (or a `failed` version on a toolbox error); 503 `AI_TOOLBOX_DISABLED` and no version while the toolbox is switched off |
| `POST review/<id>/edit/` (`{subject, body_text}`) | 201 new human version |
| `GET/POST templates/` · `GET/PUT templates/<id>/` · `GET templates/<id>/versions/` | editor; a content change creates a version; `audience` (upper-case code, blank = every audience) — a PUT without `audience` keeps the stored one, `""` clears it; the list is ordered by key, then audience |
| `POST templates/<id>/test-generate/` (`{context}`) | draft preview, nothing saved |
| `GET models/` | toolbox catalogue of the configured toolbox channel |
| `GET suppressions/?value=&scope=&source=&search=` · `POST suppressions/` · `DELETE suppressions/<id>/` | area `communicator.settings`; the channel's rows and the global rows, each with `scope` (`channel` · `install` = every channel) and `source` (`manual`, `optout_confirmed`, `unsubscribe_link`, `bounce`, `complaint`, `erased`); POST (`kind` `email` or `domain` only, source `manual`) writes a channel row, or a global one under `COMMUNICATOR_SUPPRESSION_SCOPE = "install"`; DELETE removes a channel row or a global `email` / `domain` row (then it holds nowhere) — a global `email_token` row is a 404 |
| `GET addresses/status/?email=` | area `communicator.settings`; why the channel may (not) mail the address: `refusal` (`address_invalid` · `suppressed` · null — what `communicate()` and the delivery answer), the matching suppression rows (`kind`, `scope`, `source`, `reason`, `created_at`) and `address_status` (`status`, `reason`, `verified_at`; null when nothing is known) |
| `GET/PATCH channel/` (`{mode, sandbox_mailbox}`; `live_enabled` read-only) | sending mode; `mode=live` or any `live_enabled` in a PATCH is a 400 — live is set in Django admin only |
| `GET/PUT policy/` | policy + windows (PUT replaces the windows), `timezone`, `country`, `sent_today`, `next_slot` |
| `GET messages/?status=` | outbox (default `approved`) with `next_slot` per message |
| `POST messages/<id>/send-now/` | `scheduled_at` = channel now; mode, policy and cap still apply (C-31) |
| `GET/POST sequences/` · `GET sequences/<id>/steps/` · `GET/POST sequences/<id>/texts/` | sequences, steps, text pool; `autostart` on POST (default false) |
| `PATCH sequences/<id>/` (`{is_active?, autostart?}`) | switch a sequence on or off, or make it the channel's autostart sequence; a second autostart sequence of the channel → 409 `DUPLICATE_AUTOSTART` (also on POST) |
| `PATCH sequences/<id>/texts/<text_id>/` (`{body?, is_active?}`) · `DELETE` same path | edit or restore a pool text (future follow-ups only — sent mail keeps its body); DELETE of a text no thread used → 204, deleted; of a used one → 200 with the row, `is_active=false` (the thread history stays, no thread gets the same text twice) |
| `GET threads/?subject_ref=&state=&sort=` · `GET threads/<id>/` | the inbox list: newest created first, or `sort=activity` (latest mail, draft or reply first); `state` = `draft` (a draft waits for review) · `waiting` (a mail waits for the send beat) · `replied`; each row adds `activity_at`, `subject`, `last_text` (300 chars), `reply_intent` and `needs_review` (of the newest human reply), `draft {id, subject, composed}`, `waiting {id, status, scheduled_at, next_slot}`; the page carries `counts {all, draft, waiting, replied}` (ignoring `state`) — one thread with `subject_label` (`""` when unknown), `sequence` state and `timeline` (messages + replies; `message_id` and `composed` (written by hand through `compose()`) on message entries, `reply_id` on replies (null on messages, `composed` false); `intent`, `intent_confidence`, `intent_return_date`, `needs_review` on replies; a rejected draft is on the timeline with `rejected_by` (the reviewer's name, `""` for a draft withdrawn because nobody may mail the recipient) and `reject_reason`, both `""` on every other entry) |
| `GET conversations/?state=&needs_review=true` | the Inbox list: one row per `subject_ref` (a conversation) = a `threads/` row of its newest thread (newest created) plus `thread_count` and `replied` (any thread in status replied); `state` holds when any thread is in it; `needs_review=true` keeps only conversations whose newest human reply (any thread) is flagged for a person, any other value is a 400; `draft`, `waiting` and `last_text` come from any thread (the draft to open may sit in an older one, so `draft` adds `recipient_email` of its own thread), `subject` is the newest thread's; latest activity of any thread first; `counts` count conversations (ignoring `state`, following `needs_review`) |
| `POST threads/<id>/resume-sequence/` | paused sequence runs again, thread `open` |
| `GET replies/?kind=&thread=&intent=&needs_review=` | replies, newest first; each with its class (`intent`, `intent_confidence`, `intent_source`, `intent_return_date`), `needs_review` and `plan_applied` |
| `GET/PUT reply-classes/profile/` | area `communicator.content`; prompt, model, `min_confidence` (0.7), `max_chars` (3000), `is_active` of the reply classes (PUT replaces it); GET 404 while the channel has none (no classification) |
| `GET reply-classes/plans/` · `PUT/DELETE reply-classes/plans/<intent>/` (`{sequence, notify, needs_review}`) | area `communicator.settings`; one plan per class (`is_default` = the code default); PUT replaces a class's plan, DELETE returns to the default; 400 for an unknown class, `pause_until_return` outside `out_of_office`, an `unsubscribe` plan that does not `pause`, a severity other than `low` / `medium` / `high` |
| `POST replies/<id>/confirm-optout/` · `dismiss-optout/` | decide a `suspected_optout` |
| `GET footers/` · `GET/PUT/DELETE footers/<language>/` (`{html}`) | HTML mail footer per ISO 639-1 language; PUT sanitises to an allowlist and needs `{{ legal }}` exactly once, as text — not in an attribute (else 400 on `html`); unknown language → 404; GET 404 when none |
| `GET/POST mailboxes/` · `GET/PATCH/DELETE mailboxes/<id>/` | the channel's mailboxes (replace the removed singular `mailbox/`); passwords write-only (`smtp_password_set`, `imap_password_set` on read; omitted or null = kept); PATCH changes only the fields sent; a new default takes the flag from the old one; `state` = `active` · `paused` · `needs_login` · `disabled`; a taken address or user, a non-staff `user_id` or a warm-up without `daily_cap` → 400; DELETE of the default or of a mailbox threads send from → 409 `MAILBOX_IN_USE` |
| `POST mailboxes/<id>/test/` | SMTP and IMAP login, nothing sent → `{smtp, imap}`: `ok` · `not_configured` · `no_password` · `auth_failed` · `certificate` (the host does not accept the server's certificate) · `unreachable` (the server does not answer); SMTP logs in through the host's mail backend, the connection a send uses |
| `POST mailboxes/<id>/pause/` · `resume/` (`{reason}`) | hold or release the mailbox's mail, logged with the user; 409 `MAILBOX_STATE` when already in that state |
| `GET health/` | channel and each mailbox: counts and rates for `24h` and `7d` (`sent` counts deliveries, `would_send` included), per mailbox `state`, `sent_today`, `cap_today` |
| `GET/PUT health/policy/` | auto-pause policy (`enabled`, `window_hours`, `min_sample`, `max_bounce_pct`, `max_optout_pct`, `max_complaint_pct`) |
| `GET health/log/` | pauses and resumes, newest first; `user_name` = the user's full name, else the username, `""` for an automatic pause |

## Approval inbox (all channels)

Under `/api/communicator/v2/admin/approvals/`, area `communicator.review`, same auth as above.

| Endpoint | Meaning |
|---|---|
| `GET approvals/?channel=&template_key=&search=&min_age_hours=&mailbox=` | outbound `review_required` messages of every channel, oldest first; each row carries `subject_label` (the thread's label, e.g. the company name; `""` when unknown); `search` matches the subject reference and label, the recipient address or name and the subject; `mailbox` = a mailbox id; each row carries `blocker` (the preflight detail — legal footer, a mailbox that is paused, disabled or needs a login, or the recipient: opted out or on the do-not-contact list, an invalid address, or refused by the owner of the subject through `recipient_check` — null when it may be approved; a constant number of queries per row); `counts {total, by_channel, by_mailbox}` cover the whole filtered set (`by_mailbox` key `none` = the channel SMTP entry) |
| `POST approvals/bulk/` (`{action: approve \| reject, ids, reason}`) | each id handled on its own through `review_service.accept` / `skip`; answer `{id, action, done, failed: {id: reason}}`; more than `COMMUNICATOR_BULK_APPROVAL_MAX` (100) ids → 400 and nothing changes; `failed` carries the same sentence as the row's `blocker`; every call that changed or refused a draft writes a `BulkApprovalLog` (user, action, reason, ids) |
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

List endpoints `review/`, `messages/`, `threads/`, `conversations/`, `replies/`, `health/log/`, `approvals/` and `approvals/log/` are paginated (`page`, `page_size`
≤ 100, default 20) with `count`, `next`, `previous`, `results`.

## Errors

| Status | Cause |
|---|---|
| 400 | `approvals/bulk/` over the limit; `PATCH channel/` with `mode=live` or `live_enabled` (field named in `details`); Pydantic validation (v2 error shape via `raise_pydantic_as_drf`); template model validation; unrenderable `test-generate` context; invalid suppression value |
| 401 / 403 | no or invalid JWT / not staff |
| 404 | unknown channel or object; empty review queue; no policy (`GET policy/`); no footer or unknown language (`footers/<language>/`) |
| 409 | a conflict; `error` names its kind — see the table below |
| 402 / 403 / 502 / 503 / 504 | `test-generate/` and `models/` only: toolbox budget, `MODEL_NOT_ALLOWED`, provider error or invalid draft output, toolbox not configured, timeout (`django_utils.toolbox.views.handle_toolbox_error`) |
| 503 `AI_TOOLBOX_DISABLED` | `review/<id>/rewrite/`, `test/communicate/` (AI template), `test-generate/` and `models/` while `AI_TOOLBOX_ENABLED` is `False`; body `{error, message}`, nothing is written |
| 500 `INTERNAL_ERROR` | anything that is not a refusal written by the module; body carries a `debug_id` and no exception text, the traceback is logged under the same id |

Every 409 uses the v2 error shape; `error` tells the kinds apart, `message` explains:

| `error` | When |
|---|---|
| `ALREADY_REVIEWED` | a review action (accept, skip, skip-company, rewrite, edit) on a message no longer waiting for review |
| `REVIEW_REFUSED` | rewrite of a message that is not an AI draft; accept of a message the preflight refuses (legal footer, mailbox that cannot send, recipient) |
| `MAILBOX_IN_USE` | delete of the default mailbox or of one a thread sends from |
| `MAILBOX_STATE` | pause of a paused mailbox, resume of one that is not paused |
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
| `POST test/communicate/` | `communicate()` with a template key, recipient, context, `subject_ref`, optional `subject_label`; missing footer → 400 |
| `POST test/compose/` (`{recipient, subject, body_text, subject_ref, thread_id?, in_reply_to_id?, mailbox_user_id?, subject_label?}`) | `compose()` by the requesting user → 201 `approved` or `suppressed` message; missing footer, blank or too long text, a thread or reply of another subject → 400; a thread or reply of another channel → 404 |
| `POST test/clock/` (`{iso_datetime}` or null) | freeze / clear the channel clock |
| `POST test/send-due/` | `schedule_follow_ups` then `send_due` in-process under the beat's celery-once lock (409 while held) |
| `POST test/start-sequence/` (`{thread_id, sequence_key}`) | start a sequence in a thread |
| `POST test/ingest-reply/` (`{thread_id, body, intent?, confidence?, return_date?}`) | a human reply to the thread through the normal write path (opt-out detection, plan of the class, alert, `reply_received`) with this class instead of a toolbox call; no `intent` = an unclassified reply → 201 reply |
| `POST test/poll-now/` | poll this channel's receiving mailboxes under the `poll_inbox` lock (409 while held) |
| `POST test/reset-counters/` (`{days}`) | clear the daily send counters of the channel and its mailboxes |
| `POST test/retry-drafts/` | `retry_failed_drafts` in-process under its celery-once lock (409 while held) → `{recovered, failed}` |
| `POST test/toolbox-outage/` (`{down}`) | turn the `django_utils.toolbox.outage` switch on / off; 404 where the switch is not allowed (no `DEBUG` and no `AI_TOOLBOX_TEST_SWITCH`, or production) |

## OpenAPI

`docs/openapi.yaml` is generated from a host (`manage.py spectacular`) and filtered to
`/api/communicator/` with the referenced components; `tests/test_openapi.py` validates the module's own
schema. Regenerate it whenever a request or response model changes.
