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
| `django_communicator.retry_failed_drafts` | `*/10` min | `communicator_default` | retries AI drafts that failed transiently (toolbox down, timeout, 5xx) once `status()` is `configured`; `QueueOnce`, graceful |
| `django_communicator.record_objection` | — | `communicator_default` | retry of a confirmed opt-out's agreements objection (×5, backoff) |

**After a toolbox outage** drafts ended `failed/upstream`. `retry_failed_drafts` retries each first-version
AI draft whose last failure was transient (`ToolboxConnectionError`, `ToolboxTimeoutError`, HTTP 5xx) at most
`COMMUNICATOR_DRAFT_RETRY_LIMIT` times, from the stored prompt and template version; a success moves it to
`review_required` (never approved or sent), every attempt adds a `retry N: …` line to `failure_detail`. Skipped:
budget / model / schema / auth failures, follow-ups, rewrite versions, drafts with a newer outbound message in the
thread, suppressed recipients, and drafts that failed before the retry existed (no stored prompt). An alert
fires only when a retry ends the retries (a permanent error or the limit).

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
