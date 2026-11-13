# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The approval inbox: outbound drafts waiting for a person across every channel, filters, counts, and bulk
approve / reject with a size limit and a log of who did it. Single-message review stays in `review_service`."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count, Q, QuerySet

from django_communicator import settings as communicator_settings
from django_communicator.enums import Direction, MessageStatus
from django_communicator.models import BulkApprovalLog, Message
from django_communicator.services import message_service, review_service
from django_communicator.services.exceptions import ClientError

APPROVE, REJECT = "approve", "reject"
NOT_FOUND = "message not found"


class BulkLimitError(ClientError):
    """More ids than `COMMUNICATOR_BULK_APPROVAL_MAX`."""


@dataclass(frozen=True)
class Filters:
    channel_idx: str = ""
    template_key: str = ""
    search: str = ""
    min_age_hours: float | None = None


def pending(filters: Filters, now: datetime) -> QuerySet[Message]:
    """Outbound drafts in `review_required`, oldest first. `search` matches the subject reference, the recipient
    address or name and the subject."""
    rows = Message.objects.select_related("thread__channel", "template_version__template").filter(
        status=MessageStatus.REVIEW_REQUIRED, direction=Direction.OUT
    )
    if filters.channel_idx:
        rows = rows.filter(thread__channel__idx=filters.channel_idx)
    if filters.template_key:
        rows = rows.filter(template_version__template__key=filters.template_key)
    if term := filters.search.strip():
        rows = rows.filter(_search(term))
    if filters.min_age_hours:
        rows = rows.filter(created_at__lte=now - timedelta(hours=filters.min_age_hours))
    return rows.order_by("created_at", "pk")


def _search(term: str) -> Q:
    return (
        Q(thread__subject_ref__icontains=term)
        | Q(thread__recipient_email__icontains=term)
        | Q(thread__recipient_name__icontains=term)
        | Q(subject__icontains=term)
    )


def counts_by_channel(rows: QuerySet[Message]) -> dict[str, int]:
    grouped = rows.order_by().values("thread__channel__idx").annotate(n=Count("pk"))
    return {row["thread__channel__idx"]: row["n"] for row in grouped}


def bulk(*, user, action: str, ids: list[int], reason: str = "") -> BulkApprovalLog:
    """Approve or reject each id on its own (a failing draft never blocks the others) and log the batch.

    Raises `BulkLimitError` above `COMMUNICATOR_BULK_APPROVAL_MAX` ids and `ValueError` for an unknown action."""
    if action not in (APPROVE, REJECT):
        raise ValueError(f"unknown bulk action {action!r}")
    ids = list(dict.fromkeys(ids))
    limit = communicator_settings.COMMUNICATOR_BULK_APPROVAL_MAX
    if len(ids) > limit:
        raise BulkLimitError(f"at most {limit} drafts per request")
    found = Message.objects.select_related("thread__channel", "template_version__template").in_bulk(ids)
    done, failed = [], {}
    for pk in ids:
        if (problem := _apply(found.get(pk), user, action, reason)) is None:
            done.append(pk)
        else:
            failed[str(pk)] = problem
    return BulkApprovalLog.objects.create(user=user, action=action, reason=reason, ids=ids, done=done, failed=failed)


def _apply(message: Message | None, user, action: str, reason: str) -> str | None:
    """None when done, else the client-facing reason."""
    if message is None or message.direction != Direction.OUT:
        return NOT_FOUND
    try:
        if action == APPROVE:
            review_service.accept(message, user=user)
        else:
            review_service.skip(message, user=user, reason=reason)
    except (message_service.InvalidTransitionError, review_service.ReviewError) as error:
        return error.client_message
    return None


def list_logs() -> QuerySet[BulkApprovalLog]:
    return BulkApprovalLog.objects.order_by("-created_at", "-pk")
