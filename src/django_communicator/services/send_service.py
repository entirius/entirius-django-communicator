# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The single send path: one `send_due` run over every channel with a send policy.

Due = outbound `approved`/`scheduled` with `scheduled_at` empty or reached on the channel clock. Each message is
claimed with `select_for_update(skip_locked=True)` in its own transaction, so a concurrent run never sends it twice.
"""

import logging
import random
from collections import Counter
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q, QuerySet

from django_communicator import settings as communicator_settings
from django_communicator.enums import ChannelMode, Direction, MessageStatus
from django_communicator.models import Channel, Message, SendPolicy
from django_communicator.services import alert_service, clock_service, delivery_service, policy_service
from django_communicator.services.mail_builder import SmtpNotConfiguredError
from django_communicator.services.message_service import InvalidTransitionError

logger = logging.getLogger(__name__)

DUE_STATUSES = (MessageStatus.APPROVED, MessageStatus.SCHEDULED)


def live_allowed(channel: Channel) -> bool:
    """`live` is double-gated: the channel flag and, unless the host opts out, `ENVIRONMENT == "production"`."""
    if channel.mode != ChannelMode.LIVE:
        return True
    in_production = getattr(settings, "ENVIRONMENT", "") == "production"
    return channel.live_enabled and (in_production or not communicator_settings.COMMUNICATOR_LIVE_REQUIRES_PRODUCTION)


def due_messages(channel: Channel, now: datetime) -> QuerySet[Message]:
    reached = Q(scheduled_at__isnull=True) | Q(scheduled_at__lte=now)
    return (
        Message.objects.filter(reached, thread__channel=channel, direction=Direction.OUT, status__in=DUE_STATUSES)
        .select_related("thread__channel")
        .order_by(F("scheduled_at").asc(nulls_first=True), "pk")
    )


def run_send_due(rng: random.Random | None = None) -> Counter:
    """Counts of `sent`, `would_send`, `suppressed`, `failed` and `deferred` over all channels."""
    totals = Counter({MessageStatus.SENT: 0, MessageStatus.WOULD_SEND: 0, MessageStatus.FAILED: 0, "deferred": 0})
    totals[MessageStatus.SUPPRESSED] = 0
    for policy in SendPolicy.objects.select_related("channel"):
        try:
            totals.update(_run_channel(policy.channel, rng))
        except Exception:  # noqa: BLE001 — one broken channel must not stop the others
            logger.exception("communicator send_due failed for channel %s", policy.channel.idx)
    return totals


def _run_channel(channel: Channel, rng: random.Random | None) -> Counter:
    now = clock_service.now_for(channel)
    if not live_allowed(channel):
        title = "Live sending refused"
        alert_service.notify_once(channel, kind="live_refused", severity="critical", title=title, day=now.date())
        return Counter()
    policy = policy_service.load_policy(channel)
    if policy is None:
        return Counter()
    try:
        return _send_channel(policy, now, rng)
    except SmtpNotConfiguredError:
        title = "SMTP not configured"
        alert_service.notify_once(channel, kind="smtp_missing", severity="high", title=title, day=now.date())
        return Counter()


def _send_channel(policy: SendPolicy, now: datetime, rng: random.Random | None) -> Counter:
    counts = Counter()
    for pk in list(due_messages(policy.channel, now).values_list("pk", flat=True)):
        with transaction.atomic():
            claimed = due_messages(policy.channel, now).filter(pk=pk).select_for_update(skip_locked=True, of=("self",))
            message = claimed.first()
            if message is not None:
                counts[_decide(policy, message, now, rng)] += 1
    return counts


def _decide(policy: SendPolicy, message: Message, now: datetime, rng: random.Random | None) -> str:
    """Closed window/day (C-16), cap reached (C-17) or spread → `deferred`; else deliver."""
    remaining = policy_service.remaining_today(policy, now)
    if not policy_service.should_send_now(policy, now, remaining, rng):
        return "deferred"
    return delivery_service.deliver(message, now=now)


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
