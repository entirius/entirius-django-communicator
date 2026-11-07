# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The approval inbox (outbound Q4): every draft waiting for a person across all channels, filters, and bulk
approve / reject with a size limit and a log of who did it. Single-message review stays in `review_service`."""

from collections import Counter
from datetime import datetime, timedelta

from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import Direction, MessageStatus
from django_communicator.models import BulkApprovalLog, Message, SenderRoute
from django_communicator.services import message_service, review_service
from django_communicator.services.exceptions import ClientError

ACTIONS = ("approve", "reject")


class BulkLimitError(ClientError):
    """More ids than `COMMUNICATOR_BULK_APPROVAL_MAX`."""


def pending(
    *,
    channel_idx: str = "",
    sender_route: str = "",
    template_key: str = "",
    search: str = "",
    min_age_hours: float | None = None,
    now: datetime | None = None,
) -> QuerySet[Message]:
    """Outbound drafts in `review_required`, oldest first. `sender_route` matches the effective route (the thread's
    key, else the channel default); `search` the subject reference, recipient or subject."""
    rows = (
        Message.objects.select_related("thread__channel", "template_version__template")
        .filter(status=MessageStatus.REVIEW_REQUIRED, direction=Direction.OUT)
        .order_by("created_at", "pk")
    )
    if channel_idx:
        rows = rows.filter(thread__channel__idx=channel_idx)
    if sender_route:
        rows = rows.filter(
            Q(thread__sender_route=sender_route)
            | Q(thread__sender_route="", thread__channel__default_sender_route=sender_route)
        )
    if template_key:
        rows = rows.filter(template_version__template__key=template_key)
    if search.strip():
        term = search.strip()
        rows = rows.filter(
            Q(thread__subject_ref__icontains=term)
            | Q(thread__recipient_email__icontains=term)
            | Q(thread__recipient_name__icontains=term)
            | Q(subject__icontains=term)
        )
    if min_age_hours:
        rows = rows.filter(created_at__lte=(now or timezone.now()) - timedelta(hours=min_age_hours))
    return rows


def route_map(messages: list[Message]) -> dict[tuple[int, str], SenderRoute]:
    """The routes of the messages' channels, for `sender_route_service.route_for(..., preloaded)`."""
    channel_ids = {message.thread.channel_id for message in messages}
    return {(route.channel_id, route.key): route for route in SenderRoute.objects.filter(channel_id__in=channel_ids)}


def counts(rows: QuerySet[Message]) -> dict[str, dict[str, int]]:
    """Waiting drafts per channel and per effective route key (`""` = no route)."""
    grouped = (
        rows.order_by()
        .values("thread__channel__idx", "thread__sender_route", "thread__channel__default_sender_route")
        .annotate(n=Count("pk"))
    )
    by_channel, by_route = Counter(), Counter()
    for row in grouped:
        by_channel[row["thread__channel__idx"]] += row["n"]
        by_route[row["thread__sender_route"] or row["thread__channel__default_sender_route"]] += row["n"]
    return {"by_channel": dict(by_channel), "by_route": dict(by_route)}


def bulk(*, user, action: str, ids: list[int], reason: str = "") -> BulkApprovalLog:
    """Approve or reject each id on its own (one failing draft never blocks the others) and log the batch.

    Raises `BulkLimitError` above `COMMUNICATOR_BULK_APPROVAL_MAX` ids and `ValueError` for an unknown action."""
    if action not in ACTIONS:
        raise ValueError(f"unknown bulk action {action!r}")
    ids = list(dict.fromkeys(ids))
    limit = communicator_settings.COMMUNICATOR_BULK_APPROVAL_MAX
    if len(ids) > limit:
        raise BulkLimitError(f"at most {limit} drafts per request")
    found = {
        m.pk: m
        for m in Message.objects.select_related("thread__channel", "template_version__template").filter(pk__in=ids)
    }
    done: list[int] = []
    failed: dict[str, str] = {}
    for pk in ids:
        message = found.get(pk)
        if message is None:
            failed[str(pk)] = "message not found"
            continue
        try:
            if action == "approve":
                review_service.accept(message, user=user)
            else:
                review_service.skip(message, user=user, reason=reason)
        except (message_service.InvalidTransitionError, review_service.ReviewError) as error:
            failed[str(pk)] = error.client_message
        else:
            done.append(pk)
    return BulkApprovalLog.objects.create(
        user=user, action=action, reason=reason, requested_ids=ids, done_ids=done, failed=failed
    )
