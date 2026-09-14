# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from celery_once import QueueOnce

from django_communicator.settings import QUEUE_DEFAULT


@shared_task(
    base=QueueOnce, once={"graceful": True}, queue=QUEUE_DEFAULT, name="django_communicator.schedule_follow_ups"
)
def schedule_follow_ups() -> int:
    """Beat hourly: creates due follow-ups; `send_due` delivers them under the same policy and cap."""
    from django_communicator.services import sequence_service

    return sequence_service.run_follow_ups()
