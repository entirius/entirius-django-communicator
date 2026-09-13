# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import current_app, shared_task
from celery_once import QueueOnce
from django.core.exceptions import ImproperlyConfigured

from django_communicator.settings import QUEUE_SEND


def assert_once_backend_configured() -> None:
    """Fail loud when celery-once has no backend: two concurrent runs could send the same mail (C-18)."""
    if not current_app.conf.get("ONCE"):
        raise ImproperlyConfigured("django_communicator needs celery-once: set app.conf.ONCE (REDIS_URL) on the worker")


@shared_task(base=QueueOnce, once={"graceful": True}, queue=QUEUE_SEND, name="django_communicator.send_due")
def send_due() -> dict[str, int]:
    """Beat every 5 minutes; a second run while one holds the lock exits at once."""
    from django_communicator.services import send_service

    assert_once_backend_configured()
    return dict(send_service.run_send_due())
