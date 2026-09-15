# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Configuration writes of the sending layer: send policy with windows, sequences with steps, text pools."""

from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from django_communicator.models import Channel, SendPolicy, SendWindow, Sequence, SequenceStep, TextPool


class DuplicateSequenceError(Exception):
    pass


@transaction.atomic
def save_policy(channel: Channel, *, windows: list[dict], **fields) -> SendPolicy:
    """Create or replace the channel policy; the windows are replaced as a whole."""
    policy, _ = SendPolicy.objects.update_or_create(channel=channel, defaults=fields)
    policy.windows.all().delete()
    SendWindow.objects.bulk_create(SendWindow(policy=policy, **window) for window in windows)
    return SendPolicy.objects.select_related("channel").prefetch_related("windows").get(pk=policy.pk)


def list_sequences(channel: Channel) -> QuerySet[Sequence]:
    return Sequence.objects.filter(channel=channel).prefetch_related("steps").order_by("key")


def get_sequence(channel: Channel, pk: int) -> Sequence:
    """Raises `Sequence.DoesNotExist` when it is not in this channel."""
    return Sequence.objects.prefetch_related("steps").get(channel=channel, pk=pk)


def create_sequence(channel: Channel, *, key: str, is_active: bool, steps: list[dict]) -> Sequence:
    """Raises `DuplicateSequenceError` when the key exists in the channel."""
    try:
        with transaction.atomic():
            sequence = Sequence.objects.create(channel=channel, key=key, is_active=is_active)
            SequenceStep.objects.bulk_create(SequenceStep(sequence=sequence, **step) for step in steps)
    except IntegrityError:
        raise DuplicateSequenceError(f"sequence {key} already exists") from None
    return get_sequence(channel, sequence.pk)


def list_texts(sequence: Sequence) -> QuerySet[TextPool]:
    return TextPool.objects.filter(sequence=sequence).order_by("pk")


def create_text(sequence: Sequence, *, body: str, is_active: bool) -> TextPool:
    return TextPool.objects.create(sequence=sequence, body=body, is_active=is_active)
