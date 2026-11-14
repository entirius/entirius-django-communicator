# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from celery_once import QueueOnce

from django_communicator.settings import QUEUE_DEFAULT


@shared_task(
    base=QueueOnce, once={"graceful": True}, queue=QUEUE_DEFAULT, name="django_communicator.resume_paused_sequences"
)
def resume_paused_sequences() -> int:
    """Beat hourly: sequences paused until the recipient's return run again once due."""
    from django_communicator.services import resume_service

    return resume_service.resume_due()
