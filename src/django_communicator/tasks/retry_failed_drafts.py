# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from celery_once import QueueOnce

from django_communicator.settings import QUEUE_DEFAULT


@shared_task(
    base=QueueOnce, once={"graceful": True}, queue=QUEUE_DEFAULT, name="django_communicator.retry_failed_drafts"
)
def retry_failed_drafts() -> dict[str, int]:
    """Beat every 10 minutes: AI drafts that failed transiently are retried once the toolbox is reachable."""
    from django_communicator.services import draft_retry_service

    return draft_retry_service.retry_failed_drafts()
