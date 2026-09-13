# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The only writer of `Message.status`: creation with an initial status, transitions, new versions."""

from django.db import transaction
from django.utils import timezone

from django_communicator.enums import MESSAGE_STATUS_TRANSITIONS, MessageStatus
from django_communicator.models import Message

# Copied into every new version of a message; the caller's changes override them.
VERSION_INHERITED_FIELDS = (
    "thread",
    "direction",
    "template_version",
    "render_context",
    "rendered_prompt",
    "model",
    "legal_footer",
    "requires_review",
    "automated_rewrites",
)
_REVIEW_STATUSES = frozenset({MessageStatus.APPROVED.value, MessageStatus.REJECTED.value})


class InvalidTransitionError(Exception):
    pass


def create_message(*, status: str, **fields) -> Message:
    return Message.objects.create(status=status, **fields)


def ensure_transition(message: Message, new_status: str) -> None:
    """Raises `InvalidTransitionError` unless `message.status → new_status` is a legal edge."""
    if new_status not in MESSAGE_STATUS_TRANSITIONS.get(message.status, frozenset()):
        raise InvalidTransitionError(f"invalid message transition: {message.status} -> {new_status}")


def transition(
    message: Message, new_status: str, *, user=None, reason: str = "", fields: dict | None = None
) -> Message:
    """Validate and apply a status change as compare-and-set on the stored status.

    Accept and reject stamp the reviewer, reject also the reason. A row whose status changed since `message`
    was read is not touched: `InvalidTransitionError`. `fields` are written in the same update.
    """
    ensure_transition(message, new_status)
    changes = {**(fields or {}), "status": new_status, "modified_at": timezone.now()}
    if new_status in _REVIEW_STATUSES:
        changes.update(reviewed_by=user, reviewed_at=changes["modified_at"])
    if new_status == MessageStatus.REJECTED:
        changes["reject_reason"] = reason
    with transaction.atomic():
        updated = Message.objects.filter(pk=message.pk, status=message.status).update(**changes)
    if not updated:
        raise InvalidTransitionError(f"message {message.pk} is no longer {message.status}")
    for name, value in changes.items():
        setattr(message, name, value)
    return message


def claim_for_sending(message: Message) -> Message | None:
    """Commit `approved|scheduled → sending` in a transaction of its own, before any SMTP call.

    Compare-and-set on the stored status: None when another run claimed or changed the message. Returns the row
    re-read with its thread and channel, so the delivery decides on fresh data.
    """
    ensure_transition(message, MessageStatus.SENDING)
    now = timezone.now()
    with transaction.atomic():
        claimed = Message.objects.filter(pk=message.pk, status=message.status).update(
            status=MessageStatus.SENDING, send_attempted_at=now, modified_at=now
        )
    if not claimed:
        return None
    return Message.objects.select_related("thread__channel").get(pk=message.pk)


def release_claim(message: Message, status: str) -> None:
    """Undo a `sending` claim that never reached SMTP: back to the status it was claimed from."""
    Message.objects.filter(pk=message.pk, status=MessageStatus.SENDING).update(
        status=status, modified_at=timezone.now()
    )
    message.status = status


@transaction.atomic
def claim_automated_rewrite(message: Message, *, limit: int) -> bool:
    """Count one automated rewrite on the locked row before the toolbox is called; False once `limit` is reached.

    Raises `InvalidTransitionError` when the stored message can no longer be superseded.
    """
    locked = Message.objects.select_for_update().get(pk=message.pk)
    ensure_transition(locked, MessageStatus.SUPERSEDED)
    if locked.automated_rewrites >= limit:
        return False
    message.automated_rewrites = locked.automated_rewrites + 1
    Message.objects.filter(pk=locked.pk).update(automated_rewrites=message.automated_rewrites)
    return True


@transaction.atomic
def create_version(message: Message, *, status: str = MessageStatus.REVIEW_REQUIRED, **changes) -> Message:
    """Next version of `message` (parent = message, version + 1). The old one is superseded unless the new failed.

    Either way the stored message must still be supersedable, else `InvalidTransitionError`.
    """
    locked = Message.objects.select_for_update().get(pk=message.pk)
    ensure_transition(locked, MessageStatus.SUPERSEDED)
    if status != MessageStatus.FAILED:
        transition(locked, MessageStatus.SUPERSEDED)
        message.status = locked.status
    fields = {name: getattr(locked, name) for name in VERSION_INHERITED_FIELDS}
    fields.update(changes)
    return create_message(status=status, parent=locked, version=locked.version + 1, **fields)
