# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from celery import shared_task

from django_communicator.settings import QUEUE_DEFAULT


@shared_task(
    queue=QUEUE_DEFAULT,
    name="django_communicator.record_objection",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def record_objection(channel_idx: str, email: str) -> None:
    """Retry of a confirmed opt-out's `django_agreements` objection that failed right after the confirm committed."""
    from django_communicator.services import optout_service

    optout_service.record_objection_now(channel_idx, email)
