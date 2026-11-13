# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Configuration writes of the sending layer: send policy with windows, sequences with steps, text pools."""

from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone

from django_communicator.models import (
    Channel,
    SendPolicy,
    SendWindow,
    Sequence,
    SequenceStep,
    TextPool,
    ThreadPoolUsage,
)
from django_communicator.services.exceptions import ClientError

_EDITABLE_TEXT_FIELDS = frozenset({"body", "is_active"})
_EDITABLE_SEQUENCE_FIELDS = frozenset({"is_active", "autostart"})


_AUTOSTART_TAKEN = "the channel already has an autostart sequence"


class DuplicateSequenceError(ClientError):
    pass


class DuplicateAutostartError(DuplicateSequenceError):
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


def create_sequence(
    channel: Channel, *, key: str, is_active: bool, steps: list[dict], autostart: bool = False
) -> Sequence:
    """Raises `DuplicateSequenceError` when the key exists in the channel; `DuplicateAutostartError` when the
    channel already has an autostart sequence."""
    if autostart and Sequence.objects.filter(channel=channel, autostart=True).exists():
        raise DuplicateAutostartError(_AUTOSTART_TAKEN)
    try:
        with transaction.atomic():
            sequence = Sequence.objects.create(channel=channel, key=key, is_active=is_active, autostart=autostart)
            SequenceStep.objects.bulk_create(SequenceStep(sequence=sequence, **step) for step in steps)
    except IntegrityError:
        raise DuplicateSequenceError(f"sequence {key} already exists or the channel has an autostart one") from None
    return get_sequence(channel, sequence.pk)


def update_sequence(sequence: Sequence, updates: dict[str, Any]) -> Sequence:
    """Only `is_active` and `autostart`; steps are fixed once created.

    Raises `DuplicateAutostartError` when another sequence of the channel already autostarts."""
    invalid = set(updates) - _EDITABLE_SEQUENCE_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable via update_sequence: {sorted(invalid)}")
    try:
        with transaction.atomic():
            Sequence.objects.filter(pk=sequence.pk).update(**updates, modified_at=timezone.now())
    except IntegrityError:
        raise DuplicateAutostartError(_AUTOSTART_TAKEN) from None
    return get_sequence(sequence.channel, sequence.pk)


def list_texts(sequence: Sequence) -> QuerySet[TextPool]:
    return TextPool.objects.filter(sequence=sequence).order_by("pk")


def create_text(sequence: Sequence, *, body: str, is_active: bool) -> TextPool:
    return TextPool.objects.create(sequence=sequence, body=body, is_active=is_active)


def get_text(sequence: Sequence, pk: int) -> TextPool:
    """Raises `TextPool.DoesNotExist` when it is not in this sequence."""
    return TextPool.objects.get(sequence=sequence, pk=pk)


@transaction.atomic
def update_text(text: TextPool, updates: dict[str, Any]) -> TextPool:
    """Only `body` and `is_active`; sent messages keep their rendered body, so an edit changes future follow-ups.

    Raises `TextPool.DoesNotExist` when the text was deleted since it was read."""
    invalid = set(updates) - _EDITABLE_TEXT_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable via update_text: {sorted(invalid)}")
    locked = TextPool.objects.select_for_update().get(pk=text.pk)
    for field, value in updates.items():
        setattr(locked, field, value)
    locked.save(update_fields=[*updates, "modified_at"])
    return locked


@transaction.atomic
def remove_text(text: TextPool) -> bool:
    """Delete a text no thread has used (True); deactivate a used one, keeping the thread history (False).

    The row lock serialises concurrent removals and edits only: the `ThreadPoolUsage` FK is deferred, so a
    follow-up can still insert a usage of this text meanwhile. That insert then fails at its commit and the
    follow-up scheduler retries the thread on the next tick (`sequence_service._schedule_logged`).
    """
    locked = TextPool.objects.select_for_update().get(pk=text.pk)
    if not ThreadPoolUsage.objects.filter(text=locked).exists():
        locked.delete()
        return True
    locked.is_active = False
    locked.save(update_fields=["is_active", "modified_at"])
    text.is_active = False
    return False
