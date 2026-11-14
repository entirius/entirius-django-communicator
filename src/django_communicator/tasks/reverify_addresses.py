# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from celery_once import QueueOnce

from django_communicator.settings import QUEUE_DEFAULT


@shared_task(
    base=QueueOnce, once={"graceful": True}, queue=QUEUE_DEFAULT, name="django_communicator.reverify_addresses"
)
def reverify_addresses() -> int:
    """Beat daily: up to `COMMUNICATOR_VERIFY_BATCH` stale addresses re-checked; nothing while verification is off."""
    from django_communicator import settings as communicator_settings
    from django_communicator.services import address_service

    if not communicator_settings.COMMUNICATOR_VERIFY_ENABLED:
        return 0
    return address_service.reverify_stale(communicator_settings.COMMUNICATOR_VERIFY_BATCH)
