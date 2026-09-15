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
| `POST review/<id>/rewrite/` (`{notes}`) | 201 new AI version (or a `failed` version on a toolbox error) |
| `POST review/<id>/edit/` (`{subject, body_text}`) | 201 new human version |
| `GET/POST templates/` · `GET/PUT templates/<id>/` · `GET templates/<id>/versions/` | editor; a content change creates a version |
| `POST templates/<id>/test-generate/` (`{context}`) | draft preview, nothing saved |
| `GET models/` | toolbox catalogue of the configured toolbox channel |
| `GET suppressions/?value=` · `POST suppressions/` · `DELETE suppressions/<id>/` | channel `email` / `domain` rows; the list includes global `email_token` rows, which are not deletable here |
| `GET/PATCH channel/` (`{mode, sandbox_mailbox}`; `live_enabled` read-only) | sending mode; `mode=live` or any `live_enabled` in a PATCH is a 400 — live is set in Django admin only |
| `GET/PUT policy/` | policy + windows (PUT replaces the windows), `timezone`, `country`, `sent_today`, `next_slot` |
| `GET messages/?status=` | outbox (default `approved`) with `next_slot` per message |
| `POST messages/<id>/send-now/` | `scheduled_at` = channel now; mode, policy and cap still apply (C-31) |
| `GET/POST sequences/` · `GET sequences/<id>/steps/` · `GET/POST sequences/<id>/texts/` | sequences, steps, text pool |
| `GET threads/?subject_ref=` · `GET threads/<id>/` | threads, newest first; one thread with `sequence` state and `timeline` (messages + replies; `message_id` on message entries, null on replies) |
| `POST threads/<id>/resume-sequence/` | paused sequence runs again, thread `open` |
| `GET replies/?kind=&thread=` | replies, newest first |
| `POST replies/<id>/confirm-optout/` · `dismiss-optout/` | decide a `suspected_optout` |
| `GET/PUT mailbox/` | IMAP config; `imap_password` is write-only (`has_password` on read), omitted on PUT = kept; GET 404 when none |

List endpoints `review/`, `messages/`, `threads/` and `replies/` are paginated (`page`, `page_size`
≤ 100, default 20) with `count`, `next`, `previous`, `results`.

## Errors

| Status | Cause |
|---|---|
| 400 | `PATCH channel/` with `mode=live` or `live_enabled` (field named in `details`); Pydantic validation (v2 error shape via `raise_pydantic_as_drf`); template model validation; unrenderable `test-generate` context; invalid suppression value |
| 401 / 403 | no or invalid JWT / not staff |
| 404 | unknown channel or object; empty review queue; no policy (`GET policy/`); no mailbox (`GET mailbox/`) |
| 409 | a conflict; `error` names its kind — see the table below |
| 402 / 403 / 502 / 503 / 504 | `test-generate/` and `models/` only: toolbox budget, `MODEL_NOT_ALLOWED`, provider error or invalid draft output, toolbox not configured, timeout (`django_utils.toolbox.views.handle_toolbox_error`) |

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
