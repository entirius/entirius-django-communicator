---
title: Install
description: What a host needs before the first message — prerequisites, the settings table, Celery wiring, bootstrap order.
---

Read this once before `migrate`. Day-2 work, alerts and the live checklist: `operations.md`.

## Prerequisites

| Requirement | Why | Verify |
|---|---|---|
| PostgreSQL | the only database the suite runs on (durable send claim, `select_for_update`, partial unique constraint on global suppressions) | `manage.py migrate` |
| `django_regional`, `django_notifications` (optional) before `django_communicator` in `INSTALLED_APPS` | templates and threads reference `django_regional.Language`; notifications are a soft dependency | `manage.py check` |
| `urlpatterns.append(path("", include("django_communicator.urls")))` | mounts `api/communicator/v2/admin/<channel_idx>/` | `manage.py spectacular --validate` |
| `SPECTACULAR_SETTINGS["OAS_VERSION"] = "3.1.0"` | Pydantic request/response schemas document examples as JSON Schema 2020-12 | `manage.py spectacular --validate` |
| Runtime deps in the host lock: `holidays`, `celery-once`, `redis`, `cryptography`, `entirius-django-email`, `entirius-django-utils>=2.1.0` | the wheel declares them; a host that links the module `--no-deps` does not get them | `uv lock` / `pip check` |
| Redis (`COMMUNICATOR_REDIS_URL` or `REDIS_URL`) | daily send counter | `GET policy/` answers with `sent_today` |
| celery-once backend on the Celery app (`app.conf.ONCE`) | `send_due`, `poll_inbox`, `schedule_follow_ups` run at most once at a time (C-18) | `AppConfig.ready()` raises `ImproperlyConfigured` without it — in every process that loads the app, web included |
| Workers consuming `communicator_default`, `communicator_send`, `communicator_inbound` | every task routes to one of them | `celery inspect active_queues` |
| An AI toolbox (`AI_TOOLBOX_*`, django_utils) | `ai_prompt` templates, rewrites, `models/`, `test-generate/` | `GET models/` lists the catalogue |
| `EMAIL_SMTP_CONFIGURATION_CHANNELS[<channel idx>]` (django_email) | the SMTP connection and `DEFAULT_FROM_EMAIL` of a sending channel | `manage.py check --database default --tag communicator.smtp`; missing → messages stay, `high` alert once a day |
| A stable `SECRET_KEY` | the IMAP password is encrypted with a key derived from it | rotating it empties stored passwords |

## Settings

The only settings table. Defaults live in `django_communicator/settings.py` and are read at import.

| Setting | Default | Meaning |
|---|---|---|
| `ENVIRONMENT` | — | `production` is half of the live gate; `development` mounts the `test/` endpoints and the channel clock override |
| `COMMUNICATOR_QUEUE_DEFAULT` / `_SEND` / `_INBOUND` | `communicator_default` / `communicator_send` / `communicator_inbound` | Celery queues |
| `COMMUNICATOR_REDIS_URL` | `REDIS_URL` | daily cap counter |
| `COMMUNICATOR_REQUIRE_ONCE_BACKEND` | `True` | `ready()` refuses to boot without `app.conf.ONCE` — `False` only in test settings |
| `COMMUNICATOR_SEND_INTERVAL_MIN` | `5` | beat interval of `send_due` the spread quota assumes — keep equal to the crontab |
| `COMMUNICATOR_SMTP_MAX_ATTEMPTS` | `3` | SMTP 4xx / transport attempts before `failed/smtp` |
| `COMMUNICATOR_SENDING_STALE_MINUTES` | `30` | a `sending` claim older than this ends `failed/send_outcome_unknown` |
| `COMMUNICATOR_SANDBOX_SUBJECT_PREFIX` | `"[SANDBOX] "` | subject prefix in sandbox mode |
| `COMMUNICATOR_AUTOMATED_REWRITE_LIMIT` | `3` | automated rewrites per message |
| `COMMUNICATOR_DRAFT_RETRY_LIMIT` | `3` | automatic retries of a draft that failed transiently |
| `COMMUNICATOR_INBOUND_BATCH` | `50` | mails per mailbox per poll |
| `COMMUNICATOR_INBOUND_MAX_BYTES` | `10 MB` | larger mails are quarantined unfetched |
| `COMMUNICATOR_INBOUND_BODY_MAX_CHARS` | `100000` | stored reply body cut |
| `COMMUNICATOR_SOFT_BOUNCE_RETRY_H` | `24` | delay of the one soft-bounce retry |
| `COMMUNICATOR_REPLY_INTENT_MODEL` | `""` (off) | toolbox model id for reply intent classification (T4); empty, or `AI_TOOLBOX_ENABLED = False`, = replies are never sent to the toolbox |
| `COMMUNICATOR_REPLY_INTENT_MIN_CONFIDENCE` | `0.7` | a class below this confidence is stored as `unclear` (the confidence is kept) |
| `COMMUNICATOR_REPLY_INTENT_MAX_CHARS` | `3000` | the part of a reply's own text (quote removed) sent to the toolbox |
| `COMMUNICATOR_OPTOUT_PHRASES` | `pl`, `en` lists | per-language opt-out phrases; matched on folded text (lower case, no diacritics, punctuation = one space), so a phrase is also a stem |
| `COMMUNICATOR_BULK_APPROVAL_MAX` | `100` | most drafts one bulk approve / reject call may touch (approval inbox, Q4) |
| `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` | `""` (off) | public base URL of the host (`https://api.example.com`, no trailing slash); set = `List-Unsubscribe` + `List-Unsubscribe-Post` headers and an unsubscribe link in every mail (Q6) |
| `COMMUNICATOR_PRESEND_CHECK` | `""` (off) | dotted path of a callable `(email) -> str \| None` run right before each send (e.g. `django_leads.services.verification_service.presend_check`); a reason code = the message ends `failed/presend_check` with the code as `failure_detail`; an import error or exception never blocks a send |
| `COMMUNICATOR_GMAIL_CLIENT_ID`, `COMMUNICATOR_GMAIL_CLIENT_SECRET`, `COMMUNICATOR_GMAIL_REDIRECT_URI` | `""` | OAuth client of the Google Cloud project (Gmail API transport). Read from the Django setting of the same name, else from the environment variable; never put them in the repository. Empty = connecting an account answers 409 `GMAIL_NOT_CONFIGURED`. The redirect URI is `<public API base>/api/communicator/v2/public/gmail/callback/` |
| `COMMUNICATOR_GMAIL_RETURN_URL` | `""` | where the callback sends the browser afterwards (`?gmail=connected` or an error code); empty = a plain result page |
| `COMMUNICATOR_GMAIL_API_BASE`, `_AUTH_URL`, `_TOKEN_URL`, `_REVOKE_URL` | Google's | only changed to point at the local test double (`tests/gmail_stub.py`); a non-Google API base is also what allows a sandbox channel and the test send to use the API |
| `COMMUNICATOR_GMAIL_POLL_BATCH` | `50` | inbox messages read per account per `poll_inbox` run |
| `COMMUNICATOR_AUTOREPLY_SUBJECT_PATTERNS` | out-of-office patterns (en, pl) | autoresponder subjects |
| `LEADS_ANONYMISED_DOMAIN` | `anonymised.invalid` | domain of token addresses — keep equal to django_leads |

## Celery beat

The module ships tasks, not a schedule. The host adds:

```python
app.conf.beat_schedule |= {
    "communicator-send-due": {"task": "django_communicator.send_due", "schedule": crontab(minute="*/5")},
    "communicator-follow-ups": {"task": "django_communicator.schedule_follow_ups", "schedule": crontab(minute=0)},
    "communicator-poll-inbox": {"task": "django_communicator.poll_inbox", "schedule": crontab(minute="*/5")},
    "communicator-reply-followups": {
        "task": "django_communicator.process_reply_followups",
        "schedule": crontab(minute=5),
    },
    "communicator-retry-drafts": {
        "task": "django_communicator.retry_failed_drafts",
        "schedule": crontab(minute="*/10"),
    },
}
```

`record_objection` is not scheduled — it is the retry of a failed agreements objection.

## Bootstrap order

```
manage.py migrate
# per channel, in Django admin or the admin API:
#   Channel (idx, timezone, country, languages) — stays dry_run
#   MessageTemplate(s) — static and/or ai_prompt
#   SendPolicy + SendWindow(s)          PUT policy/        (no policy = the channel never sends)
#   Suppressions                        POST suppressions/
#   MailboxConfig                       PUT mailbox/       (before sandbox, and for replies)
#   EMAIL_SMTP_CONFIGURATION_CHANNELS[<idx>] in settings
#   django_notifications Channel with the same idx (alerts are lost without it)
# then PATCH channel/ {"mode": "sandbox", "sandbox_mailbox": "..."}
```

Going `live` is a checklist, not a step: `operations.md` § Go/no-go.
