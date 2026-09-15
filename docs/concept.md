---
title: Concept
description: Channels, templates and versions, communicate(), the message lifecycle, review, sending, sequences, inbound replies, suppression and GDPR.
---

django-communicator turns "send this recipient the `lead.cold.shop` message about company 42" into a
reviewed, policy-bound, single delivery — and reads what comes back. It knows nothing about leads,
companies or legal bases: callers pass an opaque `subject_ref`, a recipient, a render context and the
legal footer; the module owns templates, drafts, review, sending and replies.

## Channel

`Channel(idx)` is the unit of configuration. Everything else hangs off it: templates, threads,
suppressions, the send policy, sequences and the IMAP mailbox. Its sending fields:

| Field | Meaning |
|---|---|
| `mode` | `dry_run` (default) · `sandbox` · `live` |
| `sandbox_mailbox` | where every `sandbox` mail goes; required for `sandbox` (C-30) |
| `live_enabled` | the channel half of the live double gate; required for `live` |
| `timezone`, `country` | channel clock and holiday calendar of the send policy — validated against `zoneinfo` and `holidays` |
| `default_language`, `languages` | template fallback language |

## Templates and versions

`MessageTemplate(channel, key, language)` is `static` (subject + body with `{placeholders}`) or
`ai_prompt` (body = system prompt, a line `=== USER ===`, user prompt; plus a toolbox `model` and an
optional `json_schema`). Every content change — subject, body, json_schema, model — goes through
`template_service.save_template` and creates the next immutable `MessageTemplateVersion`
(C-29). A message points at the version it was rendered with, so editing a template never changes an
existing draft.

`requires_legal_footer` (default on) makes `communicate()` refuse a call without a footer.
`auto_approve` exists on static templates only.

## communicate()

`services/communicate_service.communicate(*, channel_idx, template_key, recipient, context,
subject_ref, requires_review=True, thread=None) -> Message` — the one entry point for other modules.
Fixed order:

1. **Suppression** — global `email_token`, channel email, channel registrable domain → `suppressed`,
   no toolbox call (C-06).
2. **Template** — recipient language, else channel default language; none → `failed/no_template` (C-05).
3. **Legal footer** — required and blank → `LegalFooterRequiredError`, nothing created (C-03).
4. **Render** — a missing placeholder → `failed/render` naming the variables (C-04).
5. **Static** → `approved` when `auto_approve and not requires_review`, else `review_required` (C-02).
   **AI prompt** → one toolbox completion (tags `communicator.draft`, `channel:<idx>`) →
   `review_required` (C-01), or `failed` with `budget` (C-10) · `schema` (C-11) · `upstream` (C-12) ·
   `model` (C-33) and a notification. `complete()` is never retried.

The thread is the newest open `Thread(channel, subject_ref, recipient_email)`, or a new one.

## Message lifecycle

`Message` is one version of one outbound mail. `status` is written only by `services/message_service`,
as a compare-and-set against `MESSAGE_STATUS_TRANSITIONS` (`enums.py`):

```
draft ─┬─> review_required ─┬─> approved ─┬─> scheduled ─> sending ─┬─> sent ─(DSN)─> failed | scheduled
       │                    ├─> rejected  └─> sending               ├─> failed | suppressed | would_send | skipped
       │                    └─> superseded                          └─> scheduled (4xx retry)
       └─> approved | failed | rejected | superseded
```

`failed`, `suppressed`, `would_send`, `rejected`, `superseded` and `skipped` are terminal.
`failure_code` says why (`no_template`, `render`, `budget`, `schema`, `model`, `upstream`, `smtp`,
`bounce`, `send_outcome_unknown`); `failure_detail` holds the error class, codes and field names —
never the prompt.

## Review

| Action | Effect |
|---|---|
| accept | `approved`, reviewer + time, `scheduled_at` = next policy slot (empty without a policy), `message_approved` (C-07) |
| rewrite (notes) | AI drafts only: new version with the notes appended to the user prompt, previous → `superseded`; a toolbox failure creates a `failed` version and keeps the reviewed one in the queue; automated rewrites capped by `COMMUNICATOR_AUTOMATED_REWRITE_LIMIT`, failures count (C-08) |
| edit | new version `edited_by_human`, previous → `superseded`, no toolbox call (C-09) |
| skip | `rejected` with a reason; a rejected follow-up re-arms its sequence |
| skip company | skip + `company_skipped(subject_ref)` for the caller to stop the whole subject |

## Sending

One path: beat `send_due` → `send_service.run_send_due` → `delivery_service.deliver`. Only channels
with a `SendPolicy` are visited.

**Due** = outbound `approved`/`scheduled` with `scheduled_at` empty or reached on the channel clock.
"Send now" only moves `scheduled_at` to now; mode, policy and cap still apply (C-31).

**Policy** (`SendPolicy` + `SendWindow`, in the channel timezone): business days only (weekends and
`holidays` of the channel country), hour windows (start inclusive, end exclusive), a daily cap in
Redis per channel day (C-16, C-17). With `spread` the cap is released as a running quota across the
day's beat runs: `floor(daily_cap × runs elapsed / runs today) − sent today`. Each delivery reserves
its place (`INCR`, `DECR` when over) before it is attempted, so overlapping runs cannot exceed the cap.
A soft-bounce retry takes no new place.

**Modes** (C-13, C-14, C-15):

| Mode | Where the mail goes | Status |
|---|---|---|
| `dry_run` | nowhere | `would_send` |
| `sandbox` | `sandbox_mailbox`, `X-Original-To: <recipient>`, subject prefixed `[SANDBOX] ` | `sent` |
| `live` | the recipient — only while `ENVIRONMENT == "production"` **and** `live_enabled` **and** `mode == live` | `sent`; otherwise the message waits and a `critical` alert goes out once per channel day |

No setting relaxes the live gate. It is checked per message on the channel re-read with the claim; a
mode or sandbox mailbox changed between mail build and claim defers the message.

**Send-once** (C-18): suppression, follow-up blocker and the mail are checked and built first; the claim
`approved|scheduled → sending` commits on its own; SMTP runs outside any transaction; a second short
transaction records the outcome. A `sending` row older than `COMMUNICATOR_SENDING_STALE_MINUTES` is
never re-sent — it becomes `failed/send_outcome_unknown` for a human. At most once beats at least once.

**Mail** (C-32): multipart/alternative, footer after `-- `, `Message-ID:
<communicator-<id>-<hex8>@<from domain>>`, `In-Reply-To`/`References` from the thread's earlier sent
messages, SMTP connection from `EMAIL_SMTP_CONFIGURATION_CHANNELS[<channel idx>]` (django_email).

**SMTP errors** (C-19): 5xx → `failed/smtp`; the recipient is suppressed only in `live` and only when
RCPT was refused (550/551/553/554). Sender or data refusals suppress nobody and raise a `high` alert.
4xx / transport errors → `scheduled` for the next run, `failed/smtp` at `COMMUNICATOR_SMTP_MAX_ATTEMPTS`.

## Sequences

`Sequence(channel, key)` → `SequenceStep(number, days_after_previous, template_key)` + `TextPool`
texts. `ThreadSequenceState` tracks a thread's step and next due date. Beat `schedule_follow_ups`
creates the next follow-up through `communicate(requires_review=False)` with the previous context and
`body` = a random pool text not yet used in the thread; once the pool is exhausted, any text with a
warning (C-27). Follow-ups ride the same send path, policy and cap (C-28).

The next due date counts from the delivery. Terminal outcomes go through
`sequence_service.on_follow_up_finished`: delivered → next due date or `finished`
(`sequence_finished`); failed / suppressed → stopped; rejected / skipped → re-armed from the last
delivery. A reply stops the sequence; a suspected opt-out pauses it.

## Inbound

Beat `poll_inbox` reads each active `MailboxConfig` read-only (`EXAMINE`, UID cursor, UIDVALIDITY
tracked) and hands each mail to `inbound_service.ingest`, in fixed order:

1. **Duplicate** inbound Message-ID in the channel → the existing `Reply` (C-26). Our own outbound
   Message-ID (the sandbox copy) → dropped.
2. **DSN** (top-level `multipart/report; report-type=delivery-status` only): `5.x.x` → `bounce_hard`,
   message `failed/bounce`, recipient suppressed in `live` only, sequence stopped, `low` alert (C-24);
   `4.x.x` → `bounce_soft`, one retry after `COMMUNICATOR_SOFT_BOUNCE_RETRY_H`, a second →
   `failed/bounce` (C-25). A `Final-Recipient` other than the thread recipient is dropped as forged.
3. **Thread match** — our Message-ID in `In-Reply-To`/`References` (`header`, C-20), else the newest open
   thread of the sender whose last outbound predates the mail (`sender`, C-21); no match → dropped.
4. **Autoresponder** headers or subject patterns → `auto`, nothing else (C-22).
5. **Opt-out phrase** of the recipient language above the quote → `suspected_optout`, sequence paused,
   thread `replied`, `medium` alert — no suppression until a human confirms (C-23).
6. **Reply** → thread `replied`, `Message.replied_at`, sequence stopped, `reply_received`, `high` alert.

Confirm opt-out → suppression of the thread recipient, sequence `optout`, `optout_confirmed`, and a
`django_agreements` objection when installed (retried by task `record_objection`). Dismiss → kind
`reply`, sequence stays paused until `threads/<id>/resume-sequence/`.

Unreadable mail (oversized, unparseable, data error) is recorded in `InboundQuarantine` (no body) and
the cursor moves on; transient failures leave the cursor for the next beat. Mail is never deleted or
flagged.

## Suppression and GDPR

`Suppression` rows are per channel (`email`, `domain` — registrable domain) or global
(`email_token`, `channel = NULL`). The token is `utils/emails.anonymised_address` of an erased or
retention-anonymised address; the plain address is never kept. A token address itself is always
suppressed.

With django_leads installed, `contact_anonymised(email_hash, anonymised_email, subject_ref)` suppresses
the token globally and rewrites that subject's thread recipients and the subject's own reply senders.
`gdpr.py` exports and erases a subject's threads, messages, replies and suppressions; erase scrubs
subjects, bodies, footers, prompts and render contexts. A colleague's reply in the same thread is not
the subject's data.

## Signals

All sent on commit (`signals/__init__.py`): `message_approved`, `company_skipped`, `message_sent`,
`sequence_finished`, `reply_received`, `optout_confirmed`.
