# entirius-django-communicator

A Django module for channel-agnostic communication in Entirius, the open-source modular ecommerce platform.
It drafts messages from versioned templates, puts them through review, sends them under a per-channel
policy and reads the replies.

[![PyPI](https://img.shields.io/pypi/v/entirius-django-communicator.svg)](https://pypi.org/project/entirius-django-communicator/)
[![Python](https://img.shields.io/pypi/pyversions/entirius-django-communicator.svg)](https://pypi.org/project/entirius-django-communicator/)
[![License: MPL-2.0](https://img.shields.io/badge/license-MPL--2.0-blue.svg)](https://github.com/entirius/entirius-django-communicator/blob/develop/LICENSE)

## What it does

- Renders messages from templates with versions, as static text or as an AI draft.
- Holds drafts for review: accept, rewrite, edit or skip before anything is sent.
- Sends under a per-channel policy: `dry_run`, `sandbox` or `live` mode, send windows, holidays and a daily cap.
- Schedules follow-up sequences and stops them on a reply or an opt-out.
- Polls IMAP mailboxes and turns inbound mail into replies, bounces, opt-outs and suppressions.
- Exposes an admin API (JWT, Pydantic schemas, OpenAPI) for the CMS.

## Installation

```shell
uv add entirius-django-communicator
pip install entirius-django-communicator
```

Optional extra: `entirius-django-communicator[notifications]` (AI toolbox failures raise a notification
through `entirius-django-notifications`).

```python
INSTALLED_APPS = [
    # ...
    "django_utils",
    "django_regional",
    "django_email",
    "django_communicator",
]

# main/urls.py
urlpatterns.append(path("", include("django_communicator.urls")))
```

The module needs Redis (`COMMUNICATOR_REDIS_URL` or `REDIS_URL`), a celery-once backend and Celery workers
consuming the queues `communicator_default`, `communicator_send` and `communicator_inbound`. The
`COMMUNICATOR_*` settings, the beat entries and the bootstrap order: `docs/install.md` —
https://github.com/entirius/entirius-django-communicator/blob/develop/docs/install.md

## Quick start

```python
# main/urls.py
from django.urls import include, path

urlpatterns.append(path("", include("django_communicator.urls")))
```

The admin API is then mounted under `api/communicator/v2/admin/<channel_idx>/`. Settings, queues, beat and
bootstrap order: `docs/install.md`. Other modules create messages through
`django_communicator.services.communicate_service.communicate`; its contract is in
https://github.com/entirius/entirius-django-communicator/blob/develop/docs/concept.md

## Requirements

- Python 3.11 or newer, Django 4.2 or newer.
- PostgreSQL and Redis.
- Celery workers consuming `communicator_default`, `communicator_send` and `communicator_inbound`, and Celery beat.
- An AI toolbox service (the `AI_TOOLBOX_*` settings of `entirius-django-utils`) for AI drafts and rewrites.
- Entirius packages pulled in: `entirius-django-utils`, `entirius-django-regional`, `entirius-django-email`,
  `entirius-py-idx-normalizator`.

## Documentation

- Module docs: https://github.com/entirius/entirius-django-communicator/tree/develop/docs
- Entirius documentation: https://docs.entirius.com/
- Changelog: https://github.com/entirius/entirius-django-communicator/blob/develop/CHANGELOG.md

## Part of Entirius

This module runs inside the Entirius backend service (Volkanos); the leads module drafts its outreach through it.

- Backend service: https://github.com/entirius/entirius-service-volkanos
- Docker harness: https://github.com/entirius/entirius-zeno
- Sibling modules: https://github.com/entirius/entirius-django-leads, https://github.com/entirius/entirius-django-notifications,
  https://github.com/entirius/entirius-django-email

## Development

```shell
make install     # sync dependencies (uv, incl. extras)
make check       # lint + format check (ruff)
make test        # test suite (pytest; PostgreSQL through DATABASE_URL)
```

Development and agent instructions: https://github.com/entirius/entirius-django-communicator/blob/develop/AGENTS.md

## License

Mozilla Public License 2.0 (MPL-2.0) — see https://github.com/entirius/entirius-django-communicator/blob/develop/LICENSE.
