# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.apps import AppConfig, apps


class DjangoCommunicatorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_communicator"
    label = "django_communicator"
    is_volkanos = True

    def ready(self) -> None:
        from django_communicator import settings as communicator_settings
        from django_communicator.tasks.send_due import assert_once_backend_configured

        if communicator_settings.COMMUNICATOR_REQUIRE_ONCE_BACKEND:
            assert_once_backend_configured()
        if apps.is_installed("django_leads"):
            from django_communicator.signals import leads_receivers

            leads_receivers.connect()
