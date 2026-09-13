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


def transition(message: Message, new_status: str, *, user=None, reason: str = "") -> Message:
    """Validate and apply a status change; accept and reject stamp the reviewer, reject also the reason."""
    ensure_transition(message, new_status)
    message.status = new_status
    update_fields = ["status", "modified_at"]
    if new_status in _REVIEW_STATUSES:
        message.reviewed_by, message.reviewed_at = user, timezone.now()
        update_fields += ["reviewed_by", "reviewed_at"]
    if new_status == MessageStatus.REJECTED:
        message.reject_reason = reason
        update_fields.append("reject_reason")
    message.save(update_fields=update_fields)
    return message


@transaction.atomic
def create_version(message: Message, *, status: str = MessageStatus.REVIEW_REQUIRED, **changes) -> Message:
    """Next version of `message` (parent = message, version + 1). The old one is superseded unless the new failed."""
    locked = Message.objects.select_for_update().get(pk=message.pk)
    if status != MessageStatus.FAILED:
        transition(locked, MessageStatus.SUPERSEDED)
        message.status = locked.status
    fields = {name: getattr(locked, name) for name in VERSION_INHERITED_FIELDS}
    fields.update(changes)
    return create_message(status=status, parent=locked, version=locked.version + 1, **fields)
