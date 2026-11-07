---
title: Operations
description: Day 2 — tasks and queues, channel modes, alerts, what to watch, inbound quarantine, and the go/no-go checklist for switching a channel to live.
---

Install-time facts (prerequisites, settings, beat schedule, bootstrap order) are in `install.md`.

## Tasks and queues

| Task | Schedule (host) | Queue | Does |
|---|---|---|---|
| `django_communicator.send_due` | `*/5` min | `communicator_send` | the only send path; `QueueOnce`, graceful |
| `django_communicator.schedule_follow_ups` | hourly | `communicator_default` | creates due sequence follow-ups; `QueueOnce`, graceful |
| `django_communicator.poll_inbox` | `*/5` min | `communicator_inbound` | IMAP poll of every active mailbox; `QueueOnce`, graceful, retried ×3 on IMAP / socket errors |
| `django_communicator.process_reply_followups` | hourly | `communicator_default` | resumes sequences paused until a return date and sends due reminder alerts (T4); `QueueOnce`, graceful |
| `django_communicator.retry_failed_drafts` | `*/10` min | `communicator_default` | retries AI drafts that failed transiently (toolbox down, timeout, 5xx) once `status()` is `configured` — never while it is `disabled`, `unconfigured` or `unreachable`; `QueueOnce`, graceful |
| `django_communicator.record_objection` | — | `communicator_default` | retry of a confirmed opt-out's agreements objection (×5, backoff) |

**After a toolbox outage** drafts ended `failed/upstream`. `retry_failed_drafts` retries each first-version
AI draft whose last failure was transient (`ToolboxConnectionError`, `ToolboxTimeoutError`, HTTP 5xx) at most
`COMMUNICATOR_DRAFT_RETRY_LIMIT` times, from the stored prompt and template version; a success moves it to
`review_required` (never approved or sent), every attempt adds a `retry N: …` line to `failure_detail`. Skipped:
budget / model / schema / auth failures, follow-ups, rewrite versions, drafts with a newer outbound message in the
thread, suppressed recipients, and drafts that failed before the retry existed (no stored prompt). An alert
fires only when a retry ends the retries (a permanent error or the limit).

**A toolbox switched off on purpose** (`AI_TOOLBOX_ENABLED = False`) produces no failed draft, so there is nothing
to retry: `retry_failed_drafts` makes no call, drafts that failed earlier stay as they are until the switch is on,
and `schedule_follow_ups` logs `follow-up of thread <id> waits: AI toolbox disabled` at info level for each due AI
follow-up.

A queue missing from a worker's `-Q` makes its task queue forever without an error. Workers have no
autoreload — restart them after a module upgrade.

## Channel modes

| From → to | How | Refused when |
|---|---|---|
| `dry_run` → `sandbox` | `PATCH channel/` `{"mode": "sandbox", "sandbox_mailbox": "..."}` or Django admin | no `sandbox_mailbox` (409 / validation error, C-30) |
| any → `live` | `live_enabled = true` and `mode = live`, Django admin only — `PATCH channel/` answers 400 to `mode = live` or any `live_enabled` | `live_enabled` false |
| `live` → `sandbox` / `dry_run` | Django admin or `PATCH channel/` | — |

Saving `mode = live` is not sending live. Delivery also needs `ENVIRONMENT == "production"`; on any
other environment a live channel's messages wait and a `critical` "Live sending refused" alert goes out
once per channel day. Switching back to `sandbox` or `dry_run` takes effect on the next claim — a mail
built for another mode is deferred, not sent.

## Alerts

Every alert is `django_notifications.notify(channel_idx=<communicator channel idx>,
recipient_role="sales_admin", …)`. **The notifications channel must have the same `idx` as the
communicator channel** — `notify` raises for an unknown channel; communicator catches it and logs an ERROR
`communicator alert lost: communicator channel <idx> -> notifications channel <idx> …` with the severity,
subject and title, so the alert survives only in the log. Alert on that line. Without django_notifications
installed, only log lines remain.

| Severity | Title | When | Repeats |
|---|---|---|---|
| critical | Live sending refused | a `live` channel outside the live gate | once per channel day |
| high | SMTP not configured | no `EMAIL_SMTP_CONFIGURATION_CHANNELS[<idx>]` | once per channel day |
| high | SMTP refused the sender or the message | `SMTPSenderRefused` / `SMTPDataError` | once per channel day |
| high (`budget`, `model`) / medium | Message draft failed: `<code>` (`<template key>`) | toolbox failure on draft or rewrite | per message |
| high | Reply from `<sender>` | a human reply matched to a thread | per reply |
| medium | Possible opt-out | opt-out phrase detected | per reply |
| medium | IMAP poll failed | a mailbox poll failed | once per channel day |
| low | Bounce | hard bounce | per bounce |

Escalation to email / Google Chat is configured in django_notifications (`DeliveryChannelConfig`,
`EscalationRule`), not here.

## What to watch

| Signal | Meaning | Action |
|---|---|---|
| `failed/send_outcome_unknown` | a `sending` claim never recorded its outcome (worker killed mid-SMTP) | check the mailbox / SMTP log; resend by hand only if it did not leave |
| `failed/smtp` with `failure_detail` 5xx | permanent refusal | recipient suppressed in live when RCPT was refused |
| `approved`/`scheduled` piling up | cap reached, window closed, no policy, SMTP missing, live refused | `GET policy/` (`sent_today`, `next_slot`), `GET messages/` (`next_slot`) |
| `InboundQuarantine` rows (Django admin) | mail the poll could never ingest: `oversized`, `unparseable`, `data_error` — no body stored | read the mail in the mailbox by UID |
| `MailboxConfig.last_polled_at` stale | poll not running or failing | worker `-Q communicator_inbound`, "IMAP poll failed" alerts |
| `Message.delivery_note` | the last DSN that changed nothing (`delayed`, …) | informational |

The daily counter lives in Redis (`communicator:sent:<idx>:<channel day>`, 48 h TTL). Losing Redis
resets today's count — the cap can be exceeded once on that day.

## Inbound mailbox

`PUT mailbox/` (or Django admin) stores host, port, TLS, user, folder and an encrypted password.
The poll never deletes or flags mail; it moves a UID cursor. A changed host, user, folder or
UIDVALIDITY resets the cursor to 0, and per-channel dedup by Message-ID skips already known mail.
Rotating `SECRET_KEY` empties the stored password — set it again.

## Sender routes and four mailboxes (Q3, Q8)

A route (`routes/`) = From mailbox + the same person's mailbox on the other domain in CC; a thread carries only the
route key (caller sets it, blank = channel default route, none = the channel's own From). Needed outside the module:
the channel's SMTP account must be allowed to send as each From address (send-as alias or per-address permission at the
mail provider); SPF, DKIM and DMARC must cover both domains.

Replies from all four mailboxes into one thread need no new code: a reply is matched by our Message-ID in
`In-Reply-To` / `References`, not by mailbox, and our own CC copy is dropped (its Message-ID is ours). The channel has one
IMAP mailbox (`mailbox/`), so make the other three mailboxes deliver into the polled one (forward / "also deliver to" /
a shared collector or group at the mail provider). The same reply arriving twice (To and CC) is stored once.

Per-mailbox cap and warm-up live on the route: `daily_cap` limits one From address per day (routes with one From share
it; the channel `daily_cap` still applies on top). `warmup_start_cap` + `warmup_step` raise the cap each calendar day from
`warmup_started_on` up to `daily_cap` (weekends count). Off by default; `GET policy/` lists `mailboxes` with the cap of today.

## Sending health, auto-pause and the feedback loop (Q15, Q30)

`health/` counts per route and channel over 24 h and 7 d. Hard bounces come from DSNs, opt-outs from confirmed opt-outs and
unsubscribe-link clicks (attributed to the route of the address's newest sent mail), complaints from ARF reports, soft
bounces from DSNs of mails that wait for a retry. `delivered` = sent minus hard bounces. Mail of a thread without a key
counts for the channel default route; with neither it shows under the empty key and is never paused.

The auto-pause is off until `PUT health/policy/` sets `enabled`. Each `send_due` run first judges the live channel's active
routes over `window_hours`; with at least `min_sample` sent, the first of complaints, hard bounces, opt-outs above its
percent (strictly) pauses that route only: its mail stays `approved` and waits, a `high` notification and a log row are
written. There is no automatic resume: `POST routes/<key>/resume/` with a reason; afterwards only newer events count.
Sandbox and dry-run channels are never judged.

Complaints need a mail provider feedback loop that delivers ARF reports into the polled mailbox, and
`COMMUNICATOR_COMPLAINT_FEEDBACK = True` (default off, reports are then ignored). Hard bounces already suppress the
recipient on a live channel and send `message_bounced`; complaints do the same with `reason="complaint"`.
`COMMUNICATOR_SOFT_BOUNCE_MAX_ATTEMPTS` (default 2) is the number of sends before another soft bounce fails the mail.

## One-click unsubscribe and the legal footer (Q6)

Set `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` to the public API host; mails then carry `List-Unsubscribe` and
`List-Unsubscribe-Post`, plus a link in the text and HTML parts. Bulk senders need DKIM-signed mail for one-click to be
honoured. A message whose template requires a legal footer and has none ends `failed/legal_footer`; accepting it is a 409.

## Gmail API accounts (mail accounts, transport `gmail_api`)

- A sender route chooses its transport: `smtp` (the channel's SMTP connection, the default) or `gmail_api` (`users.messages.send` in
  the name of a connected mail account). The RFC 822 message is the one the SMTP path builds (headers, unsubscribe, DW in CC,
  footer, Message-ID, In-Reply-To / References); only the last step differs. The channel needs no SMTP configuration for a
  `gmail_api` route that goes out by the API.
- The API is called only in a live channel (the live double gate was checked) or in a sandbox whose `COMMUNICATOR_GMAIL_API_BASE`
  is not Google's (the test double). A dry run, and a sandbox facing the real Google, never call it: the sandbox mail goes the SMTP way.
- Tokens: the refresh token is encrypted at rest (Fernet, key from `SECRET_KEY`, as the IMAP password) and is not in any response or log; access
  tokens live in the Django cache until a minute before they expire. Changing `SECRET_KEY` makes every account `needs_reauth`.
- `needs_reauth`: Google refused the refresh token (revoked in the Google account, password reset with token revocation, admin removed the app,
  client secret changed). The account state changes, a `high` notification goes out (once a day), the account's routes stop sending (their mail stays
  `approved` and waits, no other mailbox sends it, other routes continue) until a person connects the account again. Not a permanent failure: nothing is lost.
- Failures of one send: network / 429 / 5xx / rate limit retry like a 4xx SMTP answer (`COMMUNICATOR_SMTP_MAX_ATTEMPTS`), a refusal ends
  `failed/gmail_api` with Google's reason as `failure_detail`, a request that may have reached Google (read timeout) ends
  `failed/send_outcome_unknown` and is never repeated.
- Replies: with `inbound_enabled` the beat `poll_inbox` also reads the inbox of each connected account (`history.list`, cursor `historyId`, first
  run starts the cursor) and hands each message to the same ingest as IMAP mail. A bounce or reply is therefore matched, classified and suppressed
  identically; reading a message twice changes nothing. An expired history id falls back to the newest inbox mail.
- Limits that stay Google's: Gmail / Workspace daily sending limits and SPF / DKIM / DMARC alignment of the sending domains are not removed by the API.

## Go/no-go: switching a channel to live

Tick every item per channel before `mode = live`. One unticked item = no-go. Keep the ticked copy with
the release record of that channel.

Channel: `________` · Date: `________` · Operator: `________` · Sales owner: `________`

- [ ] **Environment.** The service runs with `ENVIRONMENT = "production"` (half of the live gate; no
      other setting relaxes it).
- [ ] **Double gate set deliberately.** In Django admin (Grappelli) the channel has `live_enabled = true`
      and `mode = live` — set by the operator as the last step of this list. `GET channel/` reads
      `{"mode": "live", "live_enabled": true}`. The admin API cannot set either (`PATCH channel/` answers
      400).
- [ ] **Legal texts final.** For every legal basis × language the channel sends in, the current published
      `django_agreements` `ClauseSet` (info, opt-out and retention clauses) holds the lawyer's final text,
      not a TEST placeholder. Every template used by the channel keeps `requires_legal_footer = true`.
- [ ] **Suppression list seeded.** Known opt-outs and do-not-contact domains are in `GET suppressions/`
      (kinds `email` / `domain`) for this channel.
- [ ] **SendPolicy reviewed.** `GET policy/` shows the agreed windows, `daily_cap`, `spread` and
      `business_days_only`; the channel `timezone` and `country` (holiday calendar) are correct; the sales
      owner reviewed them.
- [ ] **SMTP and sender.** `EMAIL_SMTP_CONFIGURATION_CHANNELS[<idx>]` points at the production mail
      account with the intended `DEFAULT_FROM_EMAIL`; SPF/DKIM/DMARC for that domain pass.
- [ ] **Mailbox.** `GET mailbox/` has the production reply mailbox (`is_active`, `has_password`), and
      `poll_inbox` runs (`last_polled_at` recent), so replies, opt-outs and bounces stop the sequences.
- [ ] **Sandbox canary accepted.** The same templates (same keys and current versions), sequences and
      policy ran in `sandbox` on staging with a real model and a real sandbox mailbox; the canary report
      has zero blockers and the sales owner signed off the generated copy.
- [ ] **Monitoring recipient.** A django_notifications channel with the same `idx` exists, with an email
      and a Google Chat `DeliveryChannelConfig` and an `EscalationRule` for `high` and `critical`; a test
      notification reached both.
- [ ] **Toolbox catalogue.** The production toolbox channel lists only the intended models — `GET models/`
      shows no `fake` or test model — and a monthly budget cap is set. (zeno's `make toolbox-check` is red
      on any real model by design; on production read the catalogue instead.)
- [ ] **Workers.** Queues `communicator_send`, `communicator_inbound`, `communicator_default` are consumed;
      beat has `send_due`, `schedule_follow_ups`, `poll_inbox`; `app.conf.ONCE` is set.
- [ ] **Rollback known.** Everyone involved knows that `PATCH channel/` `{"mode": "dry_run"}` stops live
      delivery at the next claim.
