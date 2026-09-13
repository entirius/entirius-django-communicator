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

Layers one way: API → services → models. Plan 05 ships templates, `communicate()` and review; sending and inbound come later.

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
| `POST test/communicate/` | `ENVIRONMENT == "development"` only, else 404 |

## Host integration

- `INSTALLED_APPS += ["django_communicator"]` (after `django_regional`, `django_notifications`);
  `urlpatterns.append(path("", include("django_communicator.urls")))`.
- Settings: `AI_TOOLBOX_*` (utils), `COMMUNICATOR_AUTOMATED_REWRITE_LIMIT` (3), `COMMUNICATOR_QUEUE_*`,
  `COMMUNICATOR_LIVE_REQUIRES_PRODUCTION` (True).

## Gotchas

- Views and other services never save `status`; go through `message_service`.
- Static templates and suppressed recipients never reach the toolbox; `complete()` is never retried.
- `failure_detail` holds error class, toolbox code, HTTP status and field names — never the prompt.
- `utils/domains.py` is a copy of the leads/siteintel rule — never import it across modules.

## Testing end-to-end

- Host: `make check && make test` (`DATABASE_URL`, else `postgres:postgres@localhost:5432/test_communicator`);
  toolbox mocked with `django_utils.toolbox.testing.mock_toolbox`.
- Covered IDs: C-01…C-09, C-10…C-12 (consumer mapping), C-29, C-33 (`tests/test_communicate.py`, `tests/test_review.py`).
- Zeno: `make module-test MODULE=entirius-django-communicator`; BDD: `make toolbox-check && make seed &&
  make bdd TAGS=@communicator` (emporium `fixtures/django_communicator.cfg.yaml`,
  `features/communicator/communicator_draft.feature`). End-to-end guide: plan 12.
