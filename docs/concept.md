---
title: Concept
description: Channels, templates and versions, communicate(), the message lifecycle, review and approvals, sending, unsubscribe, sequences, inbound replies, complaints, suppression and GDPR.
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

`MessageTemplate(channel, key, language, audience)` is `static` (subject + body with `{placeholders}`) or
`ai_prompt` (body = system prompt, a line `=== USER ===`, user prompt; plus a toolbox `model` and an
optional `json_schema`). Every content change — subject, body, json_schema, model — goes through
`template_service.save_template` and creates the next immutable `MessageTemplateVersion`
(C-29). A message points at the version it was rendered with, so editing a template never changes an
existing draft.

`audience` narrows a template to one audience of the caller — an opaque upper-case code communicator never
interprets (leads passes the lead type). Blank serves every audience. Unique `(channel, key, language, audience)`.

`requires_legal_footer` (default on) makes `communicate()` refuse a call without a footer.
`auto_approve` exists on static templates only.

## communicate()

`services/communicate_service.communicate(*, channel_idx, template_key, recipient, context,
subject_ref, requires_review=True, thread=None, audience="", mailbox_user_id=None) -> Message` — the one entry point for other modules.
Fixed order:

1. **Can mail** — `suppression_service.can_mail`: an `invalid` address status → `suppressed` with
   `failure_detail = address_invalid`; a suppression (global `email_token`, email or registrable domain of the
   channel or global) → `suppressed` with `failure_detail = suppressed`; no toolbox call (C-06). With
   `COMMUNICATOR_VERIFY_ENABLED` an address never checked or checked longer ago than
   `COMMUNICATOR_VERIFY_MAX_AGE_DAYS` is verified first (DNS, outside any lock). The delivery asks `can_mail`
   again before it claims a message.
2. **Template** — `template_service.resolve`, most specific active template first: (recipient language,
   audience) → (recipient language, blank) → (channel default language, audience) → (channel default, blank);
   none → `failed/no_template` (C-05). A blank audience asks for the blank-audience template only.
3. **Legal footer** — required and blank → `LegalFooterRequiredError`, nothing created (C-03).
4. **Render** — a missing placeholder → `failed/render` naming the variables (C-04).
5. **Static** → `approved` when `auto_approve and not requires_review`, else `review_required` (C-02).
   **AI prompt** → one toolbox completion (tags `communicator.draft`, `channel:<idx>`) →
   `review_required` (C-01), or `failed` with `budget` (C-10) · `schema` (C-11) · `upstream` (C-12) ·
   `model` (C-33) and a notification. `complete()` is never retried.

The thread is the newest open `Thread(channel, subject_ref, recipient_email)`, or a new one. Before the thread's
first message is created its mailbox is chosen and stored in `Thread.mailbox` for good: the mailbox of
`mailbox_user_id` in the channel when it sends (`send_enabled`), else the channel's default mailbox when it sends,
else none — the thread then sends through the channel SMTP entry, as before mailboxes existed.

## Mailboxes

A channel has any number of `Mailbox` rows. Each is one From address (`address`, `display_name`, optional
`cc_address` copied on every mail) with its own SMTP login (`smtp_*`, `send_enabled`), its own IMAP login
(`imap_*`, `receive_enabled`, cursor `last_uid` / `uidvalidity`), its own daily cap with an optional warm-up and its
own pause. `user` (a staff member, at most one mailbox per user and channel) makes a person's mail leave from their
mailbox; `is_default` (one per channel) serves everyone else. `auth_method` is `password`, the only method; the
logins sit behind `mailbox_service.smtp_connection` and `imap_login`, so another method is one more branch there.

| State | When | Sending |
|---|---|---|
| `paused` | `paused_at` set — by a person or by the health policy | its mail waits (`deferred`) |
| `needs_login` | sending without an SMTP host or password, SMTP refused the login (`smtp_login_failed_at`), or receiving without an IMAP host or password | its mail waits; one `high` alert per mailbox and day |
| `disabled` | neither sending nor receiving | its mail waits |
| `active` | none of the above | sends |

A mailbox that cannot send holds its own mail only; the other mailboxes of the channel keep sending. Approval of a
draft whose mailbox cannot send is refused (preflight). Passwords are encrypted (`EncryptedTextField`) and never
returned, logged or notified. A stored password the current `SECRET_KEY` cannot decrypt reads as empty: every write
names its columns and writes a password column only when a new password is sent, so an unrelated save never wipes
the stored ciphertext; the mailbox shows `needs_login` until a password is set again.

**Health.** `HealthPolicy` (one per channel, off by default; window 168 h, sample 50, hard bounces 2 %, opt-outs
3 %, complaints 0.1 % of sent) is judged by `health_service.evaluate` at the start of every send run of a live
channel. Counts are per mailbox by `Thread.mailbox`: sent, hard and soft bounces, complaints, replies and opt-outs
(confirmed opt-outs and unsubscribe links, on the mailbox of the address's newest sent mail). A mailbox with at least
`min_sample` sent mails over a limit is paused, `MailboxPauseLog` records the measured rates and a `high` alert goes
out. Only a person resumes it (`resume/`, logged); the policy then counts events after the resume only.

## Message lifecycle

`Message` is one version of one outbound mail. `status` is written only by `services/message_service`,
as a compare-and-set against `MESSAGE_STATUS_TRANSITIONS` (`enums.py`):

```
draft ─┬─> review_required ─┬─> approved ─┬─> scheduled ─> sending ─┬─> sent ─(DSN)─> failed | scheduled
       │                    ├─> rejected  └─> sending               ├─> failed | suppressed | would_send | skipped
       │                    └─> superseded                          └─> scheduled (4xx retry)
       └─> approved | failed | rejected | superseded
```

`failed`, `suppressed`, `would_send`, `rejected`, `superseded` and `skipped` are terminal — with one exit: a
first-version AI draft that failed transiently (toolbox unreachable, timeout, 5xx) goes back to `review_required`
when the beat `retry_failed_drafts` succeeds (`message_service.recover_draft`, outside the transition table).
`failure_code` says why (`no_template`, `render`, `budget`, `schema`, `model`, `upstream`, `smtp`,
`bounce`, `send_outcome_unknown`, `legal_footer`); `failure_detail` holds the error class, codes and field names —
never the prompt.

**Toolbox switched off.** With `AI_TOOLBOX_ENABLED = False` an AI template is refused before anything is written:
`communicate()` and `review_service.rewrite()` raise `ToolboxDisabledError` — no thread, no message, no version, no
alert, no rewrite counted. Suppression, the missing-template failure and the legal-footer check run first, and static
templates work as before. A due AI follow-up keeps its step and due date and is created by the first
`schedule_follow_ups` run after the switch is turned on. A toolbox that is enabled but not configured still yields a
`failed` draft with an alert.

## Review

| Action | Effect |
|---|---|
| accept | preflight first (refused with `ReviewError` on a problem), then `approved`, reviewer + time, `scheduled_at` = next policy slot (empty without a policy), `message_approved` (C-07) |
| rewrite (notes) | AI drafts only: new version with the notes appended to the user prompt, previous → `superseded`; a toolbox failure creates a `failed` version and keeps the reviewed one in the queue; automated rewrites capped by `COMMUNICATOR_AUTOMATED_REWRITE_LIMIT`, failures count (C-08) |
| edit | new version `edited_by_human`, previous → `superseded`, no toolbox call (C-09) |
| skip | `rejected` with a reason; a rejected follow-up re-arms its sequence |
| skip company | skip + `company_skipped(subject_ref)` for the caller to stop the whole subject |

**Preflight.** `preflight_service.problem(message) -> (failure_code, detail) | None` runs at accept and again in the
delivery, before the claim. Today it checks the legal footer: required when the message's template requires one —
also a template that started to require it after the draft was made — **and for every message without a template
version**; a blank `legal_footer` then refuses the accept and fails the delivery with `failed/legal_footer`.

**Approval inbox.** `approval_service` lists the `review_required` outbound messages of every channel (filters
channel, template key, search, minimum age; counts per channel; each row with the preflight `blocker`) and
approves or rejects up to `COMMUNICATOR_BULK_APPROVAL_MAX` (100) ids at once through `review_service.accept` /
`skip`, each id on its own — a refused or vanished id lands in `failed` and never blocks the others. Every bulk call
writes a `BulkApprovalLog` (user, action, reason, ids, done, failed).

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
A soft-bounce retry takes no new place. A thread with a mailbox also reserves a place under the mailbox's
`effective_cap(mailbox, day)`: `daily_cap`, or with a warm-up `warmup_start_cap + warmup_step × days since
warmup_started_on` (calendar days), never above `daily_cap`; no `daily_cap` = only the channel cap. Redis key
`communicator:mailbox:<mailbox id>:<channel day>`; a place that did not end in `sent` is given back.

**Modes** (C-13, C-14, C-15):

| Mode | Where the mail goes | Status |
|---|---|---|
| `dry_run` | nowhere | `would_send` |
| `sandbox` | `sandbox_mailbox`, `X-Original-To: <recipient>`, subject prefixed `[SANDBOX] ` | `sent` |
| `live` | the recipient — only while `ENVIRONMENT == "production"` **and** `live_enabled` **and** `mode == live` | `sent`; otherwise the message waits and a `critical` alert goes out once per channel day |

No setting relaxes the live gate. It is checked per message on the channel re-read with the claim; a
mode or sandbox mailbox changed between mail build and claim, or a mailbox paused or disabled meanwhile, defers the
message.

**Send-once** (C-18): suppression, follow-up blocker and the mail are checked and built first; the claim
`approved|scheduled → sending` commits on its own; SMTP runs outside any transaction; a second short
transaction records the outcome. A `sending` row older than `COMMUNICATOR_SENDING_STALE_MINUTES` is
never re-sent — it becomes `failed/send_outcome_unknown` for a human. At most once beats at least once.

**Mail** (C-32): multipart/alternative, footer after `-- `, `Message-ID:
<communicator-<id>-<hex8>@<from domain>>`, `In-Reply-To`/`References` from the thread's earlier sent
messages. With a mailbox: From `display_name <address>`, CC `cc_address` (a sandbox only names it in
`X-Original-Cc`) and the mailbox's SMTP login. Without one: From `DEFAULT_FROM_EMAIL` and the SMTP connection of
`EMAIL_SMTP_CONFIGURATION_CHANNELS[<channel idx>]` (django_email). A missing or refused login is an outcome of that
message, not of the run: it waits and the run goes on with the others; a channel without its SMTP entry likewise
(`SMTP not configured`, once a day).

**Footer.** One HTML footer per (channel, language) — `MailFooter`, `footers/<language>/` — is the layout around
the legal text: signature, logo, company data, links. It carries `{{ legal }}` exactly once, as text (never inside an attribute); the caller's
`legal_footer` (leads: the agreements clause set of the legal basis) replaces it as escaped paragraphs. Saved HTML
is sanitised to an allowlist (a, img, p, br, strong, em, span, div, table family; text, colour, size and spacing
styles without `url(`/`expression(` values; http/https/mailto/tel links). At send, `mail_builder` picks the
footer of the body's language (its template version's; the thread language without one), else the
channel default language's, else none — the legal text then goes out alone, as before footers existed. The text
part is the footer converted: blocks become lines, links `text <url>`, images dropped. The sent message stores the
rendered footer in `footer_html`, so a later footer edit never rewrites sent mail.

**One-click unsubscribe.** With `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` set every mail carries `List-Unsubscribe: <url>`
and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058) and a link after the footer in the text and HTML
parts, labelled in the body language (`pl`, else English). `List-Unsubscribe` is written as one unfolded line —
Python's SMTP policy would otherwise turn the long URL into RFC 2047 encoded words no mail client reads; every other
header is written as before. The URL carries a `django.core.signing` token over `{c: channel idx, e: address}` with
its own salt and no expiry. The public route shows a confirmation page on GET; a POST suppresses the address on the
channel (reason `unsubscribe link`), stops every sequence of the address in the channel (`optout`), sends
`optout_confirmed` once per `subject_ref` and records the agreements objection. An address already suppressed for
another reason (bounce, manual) takes the reason `unsubscribe link` and gets the same steps; a second POST changes
nothing. Our own unsubscribe line quoted back in a reply is ignored by the opt-out phrase match.
Empty base URL = no headers, no link.

**SMTP errors** (C-19): 5xx → `failed/smtp`; the recipient is suppressed only in `live` and only when
RCPT was refused (550/551/553/554), followed by `message_bounced(hard=True)`. Sender or data refusals suppress nobody and raise a `high` alert.
4xx / transport errors → `scheduled` for the next run, `failed/smtp` at `COMMUNICATOR_SMTP_MAX_ATTEMPTS`.

## Sequences

`Sequence(channel, key)` → `SequenceStep(number, days_after_previous, template_key)` + `TextPool`
texts. `ThreadSequenceState` tracks a thread's step and next due date. Beat `schedule_follow_ups`
creates the next follow-up through `communicate(requires_review=False)` with the previous context and
`body` = a random pool text not yet used in the thread; once the pool is exhausted, any text with a
warning (C-27). Inactive texts are never picked. A text no thread used can be deleted; removing a used one
only deactivates it — deleting would cascade away the usage and a thread could get the same text twice.
Follow-ups ride the same send path, policy and cap (C-28).

The next due date counts from the delivery. Terminal outcomes go through
`sequence_service.on_follow_up_finished`: delivered → next due date or `finished`
(`sequence_finished`); failed / suppressed → stopped; rejected / skipped → re-armed from the last
delivery. A reply stops or pauses the sequence by the plan of its class; a suspected opt-out pauses it.

**Autostart.** A sequence with `autostart` (at most one per channel, a partial unique constraint) starts by itself:
the first delivered message of an open thread that never had a sequence state starts the channel's active
autostart sequence, armed from that delivery — only when the message was rendered from a template
(`template_version` set). A thread that ever had a state (running, paused, stopped, finished) is never restarted, and
a hand-written message never starts one.

## Inbound

Beat `poll_inbox` reads each `Mailbox` with `receive_enabled` read-only (`EXAMINE`, UID cursor, UIDVALIDITY
tracked per mailbox; each mailbox fails and is announced on its own) and hands each mail to `inbound_service.ingest`, in fixed order:

1. **Duplicate** inbound Message-ID in the channel → the existing `Reply` (C-26). Our own outbound
   Message-ID (the sandbox copy) → dropped.
2. **DSN** (top-level `multipart/report; report-type=delivery-status` only): `5.x.x` → `bounce_hard`,
   message `failed/bounce`, recipient suppressed in `live` only (then `message_bounced(hard=True)`), sequence
   stopped, `low` alert (C-24);
   `4.x.x` → `bounce_soft`, one retry after `COMMUNICATOR_SOFT_BOUNCE_RETRY_H`, a second →
   `failed/bounce` (C-25). A `Final-Recipient` other than the thread recipient is dropped as forged.
   **Complaint** (with `COMMUNICATOR_COMPLAINT_FEEDBACK` only): an ARF report (`multipart/report;
   report-type=feedback-report`) of type `abuse` or `fraud` for one of our messages → `Reply(kind=complaint,
   matched_by=feedback)`; the message status stays. On a live channel the recipient is suppressed (reason
   `complaint`), the sequence stops (`complaint`), a `high` alert goes out and `message_bounced(hard=True,
   reason="complaint")` follows. A sandbox or dry-run complaint is stored only — it says nothing about the real
   recipient. Another feedback type is dropped; `Original-Rcpt-To` is not compared (feedback loops redact it, our
   Message-ID names the recipient).
3. **Thread match** — our Message-ID in `In-Reply-To`/`References` (`header`, C-20), else the newest open
   thread of the sender whose last outbound predates the mail (`sender`, C-21); no match → dropped.
4. **Autoresponder** headers or subject patterns → `auto`, nothing else (C-22).
5. **Class** (only with an active reply class profile and the toolbox enabled, see Reply classes) — one toolbox
   call, outside any transaction; every write that follows is one transaction.
6. **Opt-out** → `suspected_optout`, sequence paused, thread `replied`, `medium` alert — no suppression until a
   human confirms (C-23). The class `unsubscribe` always is one; without a usable class (or `unclear`) the opt-out
   phrase of the recipient language above the quote decides; a confident other class with a phrase hit is one too,
   except `not_interested`, whose refusal wins over the phrase. Phrase matching runs on folded text (lower case, no
   diacritics, `ł` → `l`, any other run of characters → one space), so a phrase is a stem: "rezygnuj" covers
   "Rezygnujemy", "prosze nie pisac" covers "Proszę nie pisać".
7. **Reply** → thread `replied`, `Message.replied_at`, the plan of the class (sequence, alert, review flag),
   `reply_received`. Without a class: sequence stopped, `high` alert.

Confirm opt-out → suppression of the thread recipient, sequence `optout`, `optout_confirmed`, and a
`django_agreements` objection when installed (retried by task `record_objection`). Dismiss → kind
`reply`, sequence stays paused until `threads/<id>/resume-sequence/`.

### Reply classes

A human reply may get one of seven classes: `interested`, `not_now`, `not_interested`, `out_of_office`,
`wrong_person`, `unsubscribe`, `unclear`. The class is advice. It runs only when the channel has an active
`ReplyClassProfile` (prompt, model, `min_confidence` 0.7, `max_chars` 3000) and the toolbox is enabled; a
starting prompt ships in `fixtures/reply_class_profile.json` (the body of `PUT reply-classes/profile/`), never in
code. One completion per reply, temperature 0, a closed JSON schema (`intent`, `confidence`, `return_date`); the
reply's own text (quote removed, cut to `max_chars`) reaches the model only as data inside `<reply>…</reply>`.
Below `min_confidence` the class is `unclear` (confidence kept); `return_date` is kept only for `out_of_office`,
after today and at most a year away. Any failure — no profile, toolbox off, transport error, output outside the
schema — gives no class, and the reply is handled exactly as without classes. Stored on `Reply`: `intent` (blank
= none), `intent_confidence`, `intent_source` (`toolbox`), `intent_return_date`, `needs_review`, `plan_applied`.

The plan of a class (`ReplyClassPlan(channel, intent)`, no row = the default):

| Class | Sequence | Alert | Needs review |
|---|---|---|---|
| `interested` | stop | high | no |
| `not_now` | pause | medium | no |
| `not_interested` | stop | medium | no |
| `out_of_office` | pause_until_return | low | no |
| `wrong_person` | stop | medium | yes |
| `unsubscribe` | pause (the suspected opt-out path, always) | medium | yes |
| `unclear` | stop | high | yes |
| no class | stop | high | no |

`pause_until_return` (only for `out_of_office`): with a return date the sequence resumes the day after it at 00:00
in the channel timezone (`ThreadSequenceState.resume_at`, beat `resume_paused_sequences`, hourly), when it is still
paused, the thread is not closed, no suspected opt-out waits for a person and the address may be mailed; any
newer reply drops `resume_at`. Without a date
it is a plain pause. `plan_applied` is the snapshot of what ran (`sequence`, `notify`, `needs_review`,
`is_default`, `resume_at`); `reply_received` receivers read `reply.intent`, `reply.intent_return_date`,
`reply.needs_review` and `reply.plan_applied["notify"]`. Nothing here suppresses, reminds or moves a stage — no
plan can confirm an opt-out.

Unreadable mail (oversized, unparseable, data error) is recorded in `InboundQuarantine` (no body) and
the cursor moves on; transient failures leave the cursor for the next beat. Mail is never deleted or
flagged.

## Suppression and GDPR

`Suppression` is the one do-not-contact store. A row is a channel row or a global row (`channel = NULL`,
every channel). Kind `email_token` is always global: the token is `utils/emails.anonymised_address` of an erased
or retention-anonymised address; the plain address is never kept, and a token address itself is always
suppressed. Kinds `email` and `domain` (registrable domain) follow `COMMUNICATOR_SUPPRESSION_SCOPE`: `channel`
(default) writes channel rows, `install` global rows. The scope is an install fact, never a per-channel switch;
the reader honours global rows under both settings, so a row written under `install` keeps protecting after a
switch back, and switching never rewrites rows (`manage.py communicator_suppressions --to-install` copies channel
rows to global ones). `source` says what wrote the row (`manual`, `optout_confirmed`, `unsubscribe_link`,
`bounce`, `complaint`, `erased`); `reason` is a free note.

`AddressStatus` (one row per address, install-wide) is what is known about an address: `unknown`, `valid`,
`risky` or `invalid`, a reason code and `verified_at`. Only `invalid` blocks. A hard bounce on a live channel (a
hard DSN, an SMTP 550/551/553/554 on RCPT) writes `invalid / hard_bounce`, final; a sandbox or dry-run bounce
writes nothing. Verification (`address_service.verify`, off until `COMMUNICATOR_VERIFY_ENABLED`): syntax →
disposable domain (`COMMUNICATOR_DISPOSABLE_DOMAINS`) → DNS: no domain `domain_not_found`, null MX `null_mx`, MX
`mx_ok` (`valid`), no MX but A/AAAA `no_mx_address_fallback` (`risky`), nothing `no_mail_server`, a failed lookup
`dns_error` (`unknown`). Never an SMTP probe or an external API. `suppression_service.can_mail(channel, email)`
answers `address_invalid`, `suppressed` or None in at most two queries; `explain(channel, email)` adds the rows.

With django_leads installed, `contact_anonymised(email_hash, anonymised_email, subject_ref)` suppresses
the token globally and rewrites that subject's thread recipients and the subject's own reply senders.
Both drop the address status row. `gdpr.py` exports and erases a subject's threads, messages, replies,
suppressions and address status; erase scrubs
subjects, bodies, footers (`legal_footer`, `footer_html`), prompts and render contexts. A colleague's reply in the same thread is not
the subject's data.

## Signals

All sent on commit (`signals/__init__.py`): `message_approved`, `company_skipped`, `message_sent`,
`sequence_finished`, `reply_received`, `optout_confirmed`, `message_bounced(message, hard, reason="")` — a hard DSN,
an SMTP 5xx recipient refusal or a complaint (`reason="complaint"`), live channels only.
