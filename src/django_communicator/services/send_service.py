# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The single send path: one `send_due` run over every channel with a send policy.

Due = outbound `approved`/`scheduled` with `scheduled_at` empty or reached on the channel clock. A run may deliver
`policy_service.run_budget` messages in due order; each delivery first reserves its place under the daily cap
(atomic in Redis) and is then claimed `sending` by `delivery_service`, so overlapping runs never send twice.
"""

import logging
from collections import Counter
from datetime import date, datetime

import redis
from django.db.models import F, Q, QuerySet

from django_communicator.enums import ChannelMode, Direction, MessageStatus
from django_communicator.models import Channel, Message, SendPolicy
from django_communicator.services import (
    alert_service,
    channel_service,
    clock_service,
    counter_service,
    delivery_service,
    policy_service,
)
from django_communicator.services.mail_builder import SmtpNotConfiguredError
from django_communicator.services.message_service import InvalidTransitionError

logger = logging.getLogger(__name__)

DUE_STATUSES = (MessageStatus.APPROVED, MessageStatus.SCHEDULED)


def due_messages(channel: Channel, now: datetime) -> QuerySet[Message]:
    reached = Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now)
    return (
        Message.objects.filter(reached, thread__channel=channel, direction=Direction.OUT, status__in=DUE_STATUSES)
        .select_related("thread__channel")
        .order_by(F("scheduled_at").asc(nulls_first=True), "pk")
    )


def run_send_due() -> Counter:
    """Counts of `sent`, `would_send`, `suppressed`, `failed` and `deferred` over all channels."""
    totals = Counter({MessageStatus.SENT: 0, MessageStatus.WOULD_SEND: 0, MessageStatus.FAILED: 0, "deferred": 0})
    totals[MessageStatus.SUPPRESSED] = 0
    for policy in SendPolicy.objects.select_related("channel"):
        try:
            totals.update(_run_channel(policy.channel))
        except Exception:  # noqa: BLE001 — one broken channel must not stop the others
            logger.exception("communicator send_due failed for channel %s", policy.channel.idx)
    return totals


def _run_channel(channel: Channel) -> Counter:
    now = clock_service.now_for(channel)
    delivery_service.fail_stale_sending(channel)
    if channel.mode == ChannelMode.LIVE and not channel_service.live_allowed(channel):
        delivery_service.refuse_live(channel, now)
        return Counter()
    policy = policy_service.load_policy(channel)
    if policy is None:
        return Counter()
    try:
        return _send_channel(policy, now)
    except SmtpNotConfiguredError:
        title = "SMTP not configured"
        alert_service.notify_once(channel, kind="smtp_missing", severity="high", title=title, day=now.date())
        return Counter()


def _send_channel(policy: SendPolicy, now: datetime) -> Counter:
    """Due order; once the run budget is used up the rest waits for a later run (spread, C-17)."""
    counts, budget = Counter(), policy_service.run_budget(policy, now)
    for message in list(due_messages(policy.channel, now)):
        outcome = _deliver_within_cap(policy, message, now) if budget > 0 else delivery_service.DEFERRED
        if outcome == MessageStatus.SENT:
            budget -= 1
        counts[outcome] += 1
    return counts


def _deliver_within_cap(policy: SendPolicy, message: Message, now: datetime) -> str:
    """Reserve a place under the cap before delivering; give it back unless the message was sent.

    A soft-bounce retry took its place when it was first sent, so it does not count again.
    """
    if message.bounce_retry_at is not None:
        return delivery_service.deliver(message, now=now)
    day = policy_service.channel_day(policy, now)
    if not counter_service.reserve(policy.channel, day, policy.daily_cap):
        return delivery_service.DEFERRED
    try:
        outcome = delivery_service.deliver(message, now=now)
    except SmtpNotConfiguredError:
        _release(policy.channel, day)
        raise
    if outcome != MessageStatus.SENT:
        _release(policy.channel, day)
    return outcome


def _release(channel: Channel, day: date) -> None:
    try:
        counter_service.release(channel, day)
    except redis.RedisError:
        logger.exception("communicator could not release a cap reservation of channel %s", channel.idx)


def send_now(message: Message) -> Message:
    """C-31: only `scheduled_at` moves to the channel's now; the next beat run applies mode, policy and cap."""
    now = clock_service.now_for(message.thread.channel)
    if not Message.objects.filter(pk=message.pk, status__in=DUE_STATUSES).update(scheduled_at=now):
        raise InvalidTransitionError(f"message {message.pk} is not waiting to be sent")
    message.scheduled_at = now
    return message


def next_slot_for(message: Message, policy: SendPolicy | None) -> datetime | None:
    """When a waiting message can leave at the earliest; None when it is not waiting or the channel has no policy."""
    if policy is None or message.status not in DUE_STATUSES:
        return None
    now = clock_service.now_for(policy.channel)
    return policy_service.next_slot(policy, max(now, message.scheduled_at or now))


def initial_slot(channel: Channel) -> datetime | None:
    policy = policy_service.load_policy(channel)
    return policy_service.next_slot(policy, clock_service.now_for(channel)) if policy else None
