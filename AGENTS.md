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

Layers one way: API → services → models. Templates, `communicate()`, review and sending ship; inbound comes later.

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
  (a test greps for it). Each due message is claimed with `select_for_update(skip_locked=True)` in its own transaction.
- Due = outbound `approved`/`scheduled` with `scheduled_at` empty or reached on the channel clock
  (`clock_service.now_for`, dev-only cache override). `accept` sets `scheduled_at` = next policy slot;
  `send_now` sets it to now and nothing else.
- Policy (`SendPolicy` + `SendWindow`, tz/country from the channel): business day (`holidays`), window, daily cap
  (Redis `communicator:sent:<idx>:<channel day>`, 48 h TTL; dry_run does not count), spread = send with
  probability remaining / runs left in the window.
- Modes: `dry_run` → `would_send`, no SMTP; `sandbox` → `sandbox_mailbox`, `X-Original-To`, `[SANDBOX] ` prefix;
  `live` only with `live_enabled` and `ENVIRONMENT == "production"` — otherwise the channel is skipped and a
  critical notification goes out once per channel day. `Channel.clean()` / `channel_service.set_mode` refuse
  sandbox without mailbox and live without the flag.
- Mail (`mail_builder`): multipart/alternative, footer after `-- `, `Message-ID: <communicator-<id>-<hex8>@<from
  domain>>`, `In-Reply-To`/`References` from earlier sent messages of the thread; connection from
  `EMAIL_SMTP_CONFIGURATION_CHANNELS[<channel idx>]` via django_email — missing → messages stay, high alert once a day.
- SMTP: 5xx → `failed/smtp` (+ email suppression on 550/551/553/554 in live); 4xx / transport → `scheduled`,
  `failed/smtp` at `COMMUNICATOR_SMTP_MAX_ATTEMPTS` (`Message.send_attempts`; `attempts` stays toolbox calls).
- Sequences: `start_sequence` / `stop_sequence` / `pause_sequence`; beat `django_communicator.schedule_follow_ups`
  (hourly, `communicator_default`) creates the next follow-up via `communicate(requires_review=False)` with the
  previous context + `body` = a random unused pool text (whole pool + warning once used up). The next due date
  counts from the delivery; the delivery of the last step stops the state `finished` and emits `sequence_finished`.
- Host beat schedule: `send_due` `crontab(minute="*/5")`, `schedule_follow_ups` `crontab(minute=0)`.
  Workers need `app.conf.ONCE` (Redis) — `AppConfig.ready()` raises otherwise (`COMMUNICATOR_REQUIRE_ONCE_BACKEND`).

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
| `POST test/communicate/` · `test/clock/` (`{iso_datetime}`) · `test/send-due/` · `test/start-sequence/` | `ENVIRONMENT == "development"` only, else 404 |

## Host integration

- `INSTALLED_APPS += ["django_communicator"]` (after `django_regional`, `django_notifications`);
  `urlpatterns.append(path("", include("django_communicator.urls")))`.
- Settings: `AI_TOOLBOX_*` (utils), `COMMUNICATOR_AUTOMATED_REWRITE_LIMIT` (3), `COMMUNICATOR_QUEUE_*`,
  `COMMUNICATOR_LIVE_REQUIRES_PRODUCTION` (True), `EMAIL_SMTP_CONFIGURATION_CHANNELS` (django_email),
  `REDIS_URL` / `COMMUNICATOR_REDIS_URL`, `COMMUNICATOR_SMTP_MAX_ATTEMPTS` (3), `COMMUNICATOR_SEND_INTERVAL_MIN` (5),
  `COMMUNICATOR_SANDBOX_SUBJECT_PREFIX`, `COMMUNICATOR_REQUIRE_ONCE_BACKEND` (True).
- Runtime deps the host lock must carry: `holidays`, `celery-once`, `redis`, `entirius-django-email` (tests: `fakeredis`).

## Gotchas

- Views and other services never save `status`; go through `message_service`.
- Transitions are compare-and-set on the stored status (stale copy → 409); `message_approved` / `company_skipped`
  fire on commit. Automated rewrites are counted on the locked row before the toolbox call — failures count.
- Static templates and suppressed recipients never reach the toolbox; `complete()` is never retried.
- `failure_detail` holds error class, toolbox code, HTTP status and field names — never the prompt.
- `utils/domains.py` is a copy of the leads/siteintel rule — never import it across modules.

## Testing end-to-end

- Host: `make check && make test` (`DATABASE_URL`, else `postgres:postgres@localhost:5432/test_communicator`);
  toolbox mocked with `django_utils.toolbox.testing.mock_toolbox`.
- Covered IDs: C-01…C-09, C-10…C-12 (consumer mapping), C-29, C-33 (`tests/test_communicate.py`, `tests/test_review.py`);
  C-13…C-19, C-31, C-32 (`tests/test_sending.py`, `tests/test_policy.py`), C-27, C-28 (`tests/test_sequences.py`), C-30.
  Counter on fakeredis, mail in `django.core.mail.outbox`, celery-once on a file backend.
- Zeno: `make module-test MODULE=entirius-django-communicator`; BDD: `make toolbox-check && make seed &&
  make bdd TAGS=@communicator` (emporium `fixtures/django_communicator.cfg.yaml`,
  `features/communicator/communicator_draft.feature`, `communicator_send.feature` — `@communicator-oneshot` needs a
  fresh seed). End-to-end guide: plan 12.
