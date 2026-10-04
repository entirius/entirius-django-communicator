# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.apps import AppConfig, apps


class DjangoCommunicatorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_communicator"
    label = "django_communicator"
    is_volkanos = True
    # Copied 1:1 from entirius-django-access cf538d2 catalogue defaults;
    # the access defaults stay until this module's release.
    access_areas = [
        {"key": "communicator.review", "label": "Draft review queue"},
        {"key": "communicator.content", "label": "Templates, sequences and footers", "sensitive": ("ai_cost",)},
        {
            "key": "communicator.conversations",
            "label": "Outbox, threads, replies and suppressions",
            "sensitive": ("pii",),
        },
        {"key": "communicator.settings", "label": "Channel, send policy and mailbox", "sensitive": ("secret",)},
    ]
    # Every admin view carries its access_area; no route needs a path rule.
    access_route_rules = []

    def ready(self) -> None:
        from django_communicator import checks  # noqa: F401 — registers the configuration health checks
        from django_communicator import settings as communicator_settings
        from django_communicator.tasks.send_due import assert_once_backend_configured

        if communicator_settings.COMMUNICATOR_REQUIRE_ONCE_BACKEND:
            assert_once_backend_configured()
        if apps.is_installed("django_leads"):
            from django_communicator.signals import leads_receivers

            leads_receivers.connect()
