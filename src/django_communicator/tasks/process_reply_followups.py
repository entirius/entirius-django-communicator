# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task
from celery_once import QueueOnce

from django_communicator.settings import QUEUE_DEFAULT


@shared_task(
    base=QueueOnce, once={"graceful": True}, queue=QUEUE_DEFAULT, name="django_communicator.process_reply_followups"
)
def process_reply_followups() -> dict[str, int]:
    """Beat hourly: resumes sequences paused until a return date and sends due follow-up reminders (T4)."""
    from django_communicator.services import reply_followup_service

    return reply_followup_service.run_due()
