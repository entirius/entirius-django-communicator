# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Standalone test settings — Postgres via DATABASE_URL (zeno `make module-test` passes its own), else a neutral CI default."""

import dj_database_url

SECRET_KEY = "not so secret test secret for the communicator suite"  # noqa: S105 — test-only
DEBUG = True
ALLOWED_HOSTS = ["*"]
ENVIRONMENT = "development"
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "django_regional",
    "django_notifications",
    "django_communicator",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
ROOT_URLCONF = "tests.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "django_utils.api.v2_errors.v2_exception_handler",
}
SPECTACULAR_SETTINGS = {"TITLE": "django-communicator Admin API v2", "VERSION": "2.0.0", "OAS_VERSION": "3.1.0"}

DATABASES = {
    "default": dj_database_url.config(default="postgresql://postgres:postgres@localhost:5432/test_communicator")
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

# Toolbox calls are mocked with respx (`django_utils.toolbox.testing.mock_toolbox`) — nothing leaves the process.
AI_TOOLBOX_BASE_URL = "http://toolbox.test"
AI_TOOLBOX_API_KEY = "test-key"
AI_TOOLBOX_CHANNEL = "zeno-test"

# Mail stays in django.core.mail.outbox (pytest-django locmem); the channel entry makes django_email return a connection.
DEFAULT_FROM_EMAIL = "fallback@example.test"
EMAIL_SMTP_CONFIGURATION_CHANNELS = {
    "default-europe": {
        "EMAIL_HOST": "smtp.example.test",
        "EMAIL_PORT": 25,
        "EMAIL_HOST_USER": "",
        "EMAIL_HOST_PASSWORD": "",
        "EMAIL_USE_SSL": False,
        "EMAIL_USE_TLS": False,
        "DEFAULT_FROM_EMAIL": "outreach@mail.example.test",
    }
}
# No celery app with a once backend here; tests that run tasks configure a file backend.
COMMUNICATOR_REQUIRE_ONCE_BACKEND = False
REDIS_URL = "redis://redis.test:6379"
