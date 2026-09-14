# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import imaplib

from celery import shared_task
from celery_once import QueueOnce

from django_communicator.settings import QUEUE_INBOUND
from django_communicator.tasks.send_due import assert_once_backend_configured


@shared_task(
    base=QueueOnce,
    once={"graceful": True},
    queue=QUEUE_INBOUND,
    name="django_communicator.poll_inbox",
    autoretry_for=(imaplib.IMAP4.error, OSError),
    retry_backoff=True,
    max_retries=3,
)
def poll_inbox() -> dict[str, int]:
    """Beat every 5 minutes: ingest new mail of every active mailbox; returns `{ingested, skipped}`."""
    from django_communicator.services import poll_service

    assert_once_backend_configured()
    return dict(poll_service.poll_all())
