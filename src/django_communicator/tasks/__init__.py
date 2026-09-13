# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Celery tasks (discovered by the host's `app.autodiscover_tasks()`)."""

from django_communicator.tasks.poll_inbox import poll_inbox
from django_communicator.tasks.schedule_follow_ups import schedule_follow_ups
from django_communicator.tasks.send_due import send_due

__all__ = ["poll_inbox", "schedule_follow_ups", "send_due"]
