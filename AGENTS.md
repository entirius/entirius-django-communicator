# AGENTS.md

Channel-agnostic communication for the Volkanos platform: templates, review, sequences, send policy and inbound replies — distribution `entirius-django-communicator`, Django app `django_communicator`.

## Commands

| Command | Meaning |
|---|---|
| `make install` | sync dependencies (uv, incl. extras) |
| `make check` | lint + format-check (ruff) |
| `make fix` | auto-fix lint + format |
| `make test` | test suite (pytest + pytest-django) |

## Conventions

- English only: code, docs, commits, branches, PRs.
- MPL-2.0: every non-trivial source file carries the license header (pre-commit inserts it).
- Toolchain: uv + ruff + hatchling + pytest; all config in `pyproject.toml`; `uv.lock` committed.
- Git flow: `master` (production) + `develop` (integration); changes land via PR; semver tag on `master`.
- Never rename the package / Django app_label / DB table prefix `django_communicator` — it is a schema contract.
- Migrations are part of the public contract — never edit an already released migration.
- Default: do not commit — git is the user's call.

## Commit Message Format

**NEVER add `Co-Authored-By: Claude ...` (or any other Claude/Anthropic attribution) to commit messages.**

This overrides the default Claude Code behavior of appending a `Co-Authored-By` trailer. Commit messages MUST contain only the user's authored content — no robot footer, no "Generated with Claude Code" line, no co-author trailer.

Same rule applies to PR descriptions: no `Generated with [Claude Code]` footer.

## Architecture

Layers one way: API → services → models. Templates, `communicate()`, review, sending and inbound ship.

- `models/` — `Channel` (`idx`, mode/sandbox/live flags for the sending layer), `MessageTemplate` (channel + key +
  language, `static` | `ai_prompt`, `current_version`), `MessageTemplateVersion` (immutable content snapshot),
  `Thread` (channel + opaque `subject_ref` + recipient), `Message` (one version; `parent` chain), `Suppression`.
- `services/communicate_service.communicate(*, channel_idx, template_key, recipient, context, subject_ref,
  requires_review=True, thread=None) -> Message`: suppression (email / registrable domain) → `suppressed`;
  template by recipient language → channel default → `failed/no_template`; footer required and empty →
  `LegalFooterRequiredError` (nothing created); missing placeholder → `failed/render`; static → `approved` when
  `auto_approve and not requires_review`, else `review_required`; ai_prompt → one toolbox completion →
  `review_required`, or `failed` with `budget|schema|model|upstream` + `notify(recipient_role="sales_admin")`.
- `services/message_service` — the only writer of `Message.status` (`MESSAGE_STATUS_TRANSITIONS` in `enums.py`).
- `services/review_service` — accept (`message_approved`), rewrite (notes appended to the user prompt, automated
  rewrites capped by `COMMUNICATOR_AUTOMATED_REWRITE_LIMIT`), edit (`edited_by_human`, no toolbox), skip, skip company
  (`company_skipped(subject_ref)`).
- `services/template_service.save_template` — every content change (subject, body, json_schema, model) creates the
  next version; drafts keep the version they were rendered with.
- AI prompt body: system prompt, a line `=== USER ===`, user prompt. Output schema `{subject, body_paragraphs[]}`.
- Toolbox tags: `communicator.draft` | `communicator.rewrite` | `communicator.test_generate` + `channel:<idx>`.
- Soft dependency: extra `notifications` — without it a failed draft only logs a warning.

## Sending

- One send path: beat `django_communicator.send_due` (every 5 min, queue `communicator_send`, `QueueOnce`
  graceful) → `services/send_service.run_send_due` → `delivery_service.deliver` — nothing else calls `deliver(`
  (a test greps for it).
- Send-once (at most once beats at least once): `deliver` commits the claim `approved|scheduled → sending`
  (`send_attempted_at`) as a compare-and-set of its own, calls SMTP outside any transaction, then a second short
  transaction records `sent`/`failed`/`scheduled`. A `sending` row older than `COMMUNICATOR_SENDING_STALE_MINUTES`
  (30) is never re-sent — the next run marks it `failed/send_outcome_unknown` for a human.
- Due = outbound `approved`/`scheduled` with `scheduled_at` empty or reached on the channel clock
  (`clock_service.now_for`, dev-only cache override). `accept` sets `scheduled_at` = next policy slot;
  `send_now` sets it to now and nothing else.
- Policy (`SendPolicy` + `SendWindow`, tz/country from the channel): business day (`holidays`), window, daily cap
  (Redis `communicator:sent:<idx>:<channel day>`, 48 h TTL; dry_run does not count). A run delivers at most
  `run_budget` = remaining cap (spread off) or `ceil(remaining / runs left in the window)` (spread on), in due order;
  each delivery reserves its place first (`INCR`, `DECR` + deferred when over the cap, given back unless sent), so
  overlapping runs cannot exceed the cap.
- Modes: `dry_run` → `would_send`, no SMTP; `sandbox` → `sandbox_mailbox`, `X-Original-To`, `[SANDBOX] ` prefix;
  `live` only while `channel_service.live_allowed` holds (`ENVIRONMENT == "production"` and `live_enabled` and
  `mode == live`, no setting relaxes it) — checked per message on the channel re-read with the claim (the run-level
  check is only a shortcut); otherwise the message stays waiting and a critical notification goes out once per
  channel day. `Channel.clean()` / `channel_service.set_mode` refuse
  sandbox without mailbox and live without the flag.
- Mail (`mail_builder`): multipart/alternative, footer after `-- `, `Message-ID: <communicator-<id>-<hex8>@<from
  domain>>`, `In-Reply-To`/`References` from earlier sent messages of the thread; connection from
  `EMAIL_SMTP_CONFIGURATION_CHANNELS[<channel idx>]` via django_email — missing → messages stay, high alert once a day.
- SMTP: 5xx → `failed/smtp`; the recipient is suppressed (live) only when RCPT was refused
  (`SMTPRecipientsRefused` 550/551/553/554) — `SMTPSenderRefused`/`SMTPDataError` suppress nobody and raise a high
  channel alert once per day; 4xx / transport → `scheduled`,
  `failed/smtp` at `COMMUNICATOR_SMTP_MAX_ATTEMPTS` (`Message.send_attempts`; `attempts` stays toolbox calls).
- Sequences: `start_sequence` / `stop_sequence` / `pause_sequence`; beat `django_communicator.schedule_follow_ups`
  (hourly, `communicator_default`) creates the next follow-up via `communicate(requires_review=False)` with the
  previous context + `body` = a random unused pool text (whole pool + warning once used up). The next due date
  counts from the delivery; the delivery of the last step stops the state `finished` and emits `sequence_finished`.
  Follow-ups carry `Message.sequence_step`; the step advance is a compare-and-set on `step` (overlapping runs create
  one follow-up). Every terminal outcome goes through `sequence_service.on_follow_up_finished`: delivered → next due
  date / `finished`; failed or suppressed → stopped `failed`/`suppressed`; rejected or skipped → re-armed from the
  last delivery. `deliver` skips a follow-up (`skipped`, `failure_detail` = `thread_replied` |
  `sequence_not_running`) once the thread is no longer open or its sequence is stopped/paused.
- Channel `country` (known to `holidays`) and `timezone` (known to `zoneinfo`) are validated in `clean()` and
  `save()`; a bad stored value makes `load_policy` raise `ChannelConfigError` → every admin view answers 409
  `CHANNEL_CONFIG_INVALID` (policy, outbox, accept), never 500.
- Host beat schedule (service `main/celery.py`, plan 12): `send_due` `crontab(minute="*/5")`, `schedule_follow_ups`
  `crontab(minute=0)`.
  Workers need `app.conf.ONCE` (Redis) — `AppConfig.ready()` raises otherwise (`COMMUNICATOR_REQUIRE_ONCE_BACKEND`).

## Inbound

- Beat `django_communicator.poll_inbox` (`crontab(minute="*/5")`, queue `communicator_inbound`, `QueueOnce` graceful,
  autoretry on `IMAP4.error`/`OSError` ×3) → `services/poll_service.poll_all`: per active `MailboxConfig`
  (one per channel, password `EncryptedTextField` keyed from `SECRET_KEY`) EXAMINE (read-only) the folder,
  `UID SEARCH last_uid+1:*`, at most `COMMUNICATOR_INBOUND_BATCH` mails, `last_uid` advanced after every mail
  (an unreadable mail is logged and skipped). A highest UID below the cursor = UIDVALIDITY change → cursor 0 + warning.
  IMAP failure → `notify(medium, "IMAP poll failed")` once per channel day, then the error re-raises for the retry.
  Mail is never deleted or flagged; credentials and bodies are never logged.
- `services/inbound_service.ingest(channel, raw) -> Reply | None`, fixed order:
  1. duplicate inbound Message-ID (missing → `<sha256-…@communicator>`) → the existing `Reply`, nothing else (C-26);
     our own outbound Message-ID (sandbox copy in the mailbox) → dropped;
  2. DSN (`multipart/report; report-type=delivery-status` or a `message/delivery-status` part; original id from
     `Original-Message-ID` → report `In-Reply-To`/`References` → returned message) — `5.x.x`: `bounce_hard`,
     message `failed/bounce`, email suppressed, sequence stopped `bounce`, `notify(low)` (C-24); `4.x.x`:
     `bounce_soft`, first → `scheduled` at now + `COMMUNICATOR_SOFT_BOUNCE_RETRY_H`, second → `failed/bounce` (C-25).
     DSNs never change `Thread.status`; an unmatched DSN is dropped;
  3. thread: our Message-ID in `In-Reply-To`/`References` (`header`, C-20), else the newest open thread whose
     recipient is the sender (`sender`, C-21); no match → dropped (logged, not stored);
  4. autoresponder (`Auto-Submitted` ≠ no, `X-Autoreply`, `X-Autorespond`, `Precedence` auto_reply/bulk/junk, subject
     patterns) → `auto`, nothing else (C-22) — beats an opt-out phrase;
  5. opt-out phrase of the recipient language (all languages without one) in the first 2 000 body chars →
     `suspected_optout`, sequence paused, thread `replied`, `notify(medium)` — no suppression (C-23);
  6. reply → thread `replied`, `Message.replied_at`, sequence stopped `replied`, `reply_received`, `notify(high)`.
- `services/optout_service.confirm(reply, user)` → email suppression, sequence `optout`, `optout_confirmed(subject_ref,
  email, channel_idx)`, `django_agreements` `record_objection` when installed (soft, `functools.cache` guard);
  `dismiss(reply)` → kind `reply`, `reply_received`, no notification, the sequence stays paused.
- `Reply.raw_headers` holds headers only; attachments are never stored.

## Admin API v2

Prefix `api/communicator/v2/admin/<channel_idx>/`, `JWTAuthentication` + `IsAdminUser`:

| Endpoint | Meaning |
|---|---|
| `GET review/?status=&page=` / `GET review/next/` | queue oldest first / oldest `review_required` (404 when empty) |
| `POST review/<id>/accept/` · `skip/` · `skip-company/` (`{reason}`) | 200; illegal transition → 409 |
| `POST review/<id>/rewrite/` (`{notes}`) · `edit/` (`{subject, body_text}`) | 201 new version |
| `GET/POST templates/`, `GET/PUT templates/<id>/`, `GET templates/<id>/versions/` | editor; PUT versions content |
| `POST templates/<id>/test-generate/` (`{context}`) | draft without saving; toolbox errors keep their status |
| `GET models/` | toolbox catalogue passthrough |
| `GET/POST suppressions/`, `DELETE suppressions/<id>/` | duplicate → 409 |
| `GET/PATCH channel/` (`{mode, sandbox_mailbox, live_enabled}`) | unsafe mode combination → 409 |
| `GET/PUT policy/` | policy + windows (PUT replaces), `sent_today`, `next_slot` |
| `GET messages/?status=` · `POST messages/<id>/send-now/` | outbox with `next_slot`; send now of a non-waiting message → 409 |
| `GET/POST sequences/`, `GET sequences/<id>/steps/`, `GET/POST sequences/<id>/texts/` | duplicate key → 409 |
| `GET threads/?subject_ref=` · `GET threads/<id>/` | thread list; one thread with its `timeline` (messages + replies, 4 queries) |
| `GET replies/?kind=&thread=` | newest first |
| `POST replies/<id>/confirm-optout/` · `dismiss-optout/` | 200; not an undecided `suspected_optout` → 409 |
| `GET/PUT mailbox/` | IMAP config; `imap_password` write-only (`has_password`), omitted on PUT = kept |
| `POST test/communicate/` · `test/clock/` (`{iso_datetime}`) · `test/send-due/` · `test/start-sequence/` · `test/poll-now/` · `test/reset-counters/` (`{days}`) | `ENVIRONMENT == "development"` only, else 404; `send-due` takes the beat's celery-once lock (409 while held) |

## Host integration

- `INSTALLED_APPS += ["django_communicator"]` (after `django_regional`, `django_notifications`);
  `urlpatterns.append(path("", include("django_communicator.urls")))`.
- Settings: `AI_TOOLBOX_*` (utils), `COMMUNICATOR_AUTOMATED_REWRITE_LIMIT` (3), `COMMUNICATOR_QUEUE_*`,
  `ENVIRONMENT` (live needs `production`), `EMAIL_SMTP_CONFIGURATION_CHANNELS` (django_email),
  `REDIS_URL` / `COMMUNICATOR_REDIS_URL`, `COMMUNICATOR_SMTP_MAX_ATTEMPTS` (3), `COMMUNICATOR_SENDING_STALE_MINUTES` (30), `COMMUNICATOR_SEND_INTERVAL_MIN` (5),
  `COMMUNICATOR_SANDBOX_SUBJECT_PREFIX`, `COMMUNICATOR_REQUIRE_ONCE_BACKEND` (True), `COMMUNICATOR_INBOUND_BATCH` (50),
  `COMMUNICATOR_SOFT_BOUNCE_RETRY_H` (24), `COMMUNICATOR_OPTOUT_PHRASES`, `COMMUNICATOR_AUTOREPLY_SUBJECT_PATTERNS`.
- Runtime deps the host lock must carry: `holidays`, `celery-once`, `redis`, `entirius-django-email`, `cryptography`
  (tests: `fakeredis`).
- Host beat schedule adds `poll_inbox` `crontab(minute="*/5")`; workers consume `communicator_inbound`.

## Gotchas

- Views and other services never save `status`; go through `message_service`.
- Transitions are compare-and-set on the stored status (stale copy → 409); `message_approved` / `company_skipped`
  fire on commit. Automated rewrites are counted on the locked row before the toolbox call — failures count.
- Static templates and suppressed recipients never reach the toolbox; `complete()` is never retried.
- `failure_detail` holds error class, toolbox code, HTTP status and field names — never the prompt.
- `utils/domains.py` is a copy of the leads/siteintel rule, `utils/encryption.py` + `encrypted_field.py` of
  contact_forms — never import them across modules. Rotating `SECRET_KEY` empties stored IMAP passwords.
- `Thread.status` is written only by `inbound_service` (and the plan 05 close action). There is no resume endpoint
  for a paused sequence yet — a dismissed opt-out stays paused.

## Testing end-to-end

- Host: `make check && make test` (`DATABASE_URL`, else `postgres:postgres@localhost:5432/test_communicator`);
  toolbox mocked with `django_utils.toolbox.testing.mock_toolbox`.
- Covered IDs: C-01…C-09, C-10…C-12 (consumer mapping), C-29, C-33 (`tests/test_communicate.py`, `tests/test_review.py`);
  C-13…C-19, C-31, C-32 (`tests/test_sending.py`, `tests/test_policy.py`), C-27, C-28 (`tests/test_sequences.py`), C-30; C-20…C-26 (`tests/test_inbound.py` on
  `tests/fixtures/mail/*.eml`, copies of the emporium fixtures; imaplib faked).
  Counter on fakeredis, mail in `django.core.mail.outbox`, celery-once on a file backend.
- Zeno: `make module-test MODULE=entirius-django-communicator`; BDD: `make toolbox-check && make seed &&
  make bdd TAGS=@communicator` (emporium `fixtures/django_communicator.cfg.yaml`,
  `features/communicator/communicator_draft.feature`, `communicator_send.feature`, `communicator_inbound.feature` (C-20…C-24,
  needs `make mail`) — `@communicator-oneshot` needs a
  fresh seed). End-to-end guide: plan 12.
