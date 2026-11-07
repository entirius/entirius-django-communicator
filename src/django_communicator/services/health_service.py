# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Sending health per sender route and per channel (outbound Q15) and the automatic pause of a route.

Counts are events inside a window: mails sent (`sent_at`), hard bounces, soft bounces (a DSN for a message that waits
for a retry), complaints (ARF, Q30), opt-outs (confirmed opt-out or unsubscribe link, attributed to the route of the
recipient's newest sent mail) and human replies. Rates are percent of `sent` in the same window. `delivered` is
`sent` minus hard bounces. A mail whose thread names no route belongs to the channel's default route.

The auto-pause is off until a `HealthPolicy` is enabled; it judges live channels only (a sandbox bounce says nothing
about real recipients), pauses one route (`SenderRoute.paused_at`) and never resumes by itself.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q

from django_communicator.enums import (
    ChannelMode,
    Direction,
    HealthTrigger,
    MessageStatus,
    ReplyKind,
    RouteLogAction,
    SuppressionKind,
)
from django_communicator.models import Channel, HealthPolicy, Message, Reply, RoutePauseLog, SenderRoute, Suppression
from django_communicator.services import alert_service
from django_communicator.services.exceptions import ClientError

logger = logging.getLogger(__name__)

OPTOUT_REASONS = ("unsubscribe link", "optout confirmed")
COUNTERS = ("sent", "hard_bounces", "soft_bounces", "complaints", "optouts", "replies")
WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}


class RouteStateError(ClientError):
    """The route is already in the state the request asks for."""


def get_policy(channel: Channel) -> HealthPolicy:
    """The channel's policy; an unsaved default (disabled) when none was stored."""
    return HealthPolicy.objects.filter(channel=channel).first() or HealthPolicy(channel=channel)


def save_policy(channel: Channel, **fields) -> HealthPolicy:
    policy, _ = HealthPolicy.objects.update_or_create(channel=channel, defaults=fields)
    return policy


def _key(channel: Channel, raw: str) -> str:
    return raw or channel.default_sender_route


def counts(channel: Channel, since: datetime) -> dict[str, dict[str, int]]:
    """{route key: {counter: n}} for events after `since`; the key is "" for mail without any route."""
    result: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(COUNTERS, 0))

    def add(raw_key: str, counter: str, n: int) -> None:
        result[_key(channel, raw_key)][counter] += n

    sent = Q(status=MessageStatus.SENT) | Q(status=MessageStatus.FAILED, failure_code="bounce")
    rows = (
        Message.objects.filter(sent, thread__channel=channel, direction=Direction.OUT, sent_at__gt=since)
        .values("thread__sender_route")
        .annotate(n=Count("pk"))
    )
    for row in rows:
        add(row["thread__sender_route"], "sent", row["n"])
    soft = Q(kind=ReplyKind.BOUNCE_SOFT, message__bounce_retry_at__isnull=False)
    kinds = Q(kind__in=[ReplyKind.BOUNCE_HARD, ReplyKind.COMPLAINT, ReplyKind.REPLY]) | soft
    replies = (
        Reply.objects.filter(kinds, channel=channel, received_at__gt=since)
        .values("kind", "thread__sender_route")
        .annotate(n=Count("pk"))
    )
    names = {
        ReplyKind.BOUNCE_HARD: "hard_bounces",
        ReplyKind.BOUNCE_SOFT: "soft_bounces",
        ReplyKind.COMPLAINT: "complaints",
        ReplyKind.REPLY: "replies",
    }
    for row in replies:
        add(row["thread__sender_route"], names[row["kind"]], row["n"])
    for route_key, n in _optouts_by_route(channel, since).items():
        add(route_key, "optouts", n)
    return result


def _optouts_by_route(channel: Channel, since: datetime) -> dict[str, int]:
    emails = list(
        Suppression.objects.filter(
            channel=channel, kind=SuppressionKind.EMAIL, reason__in=OPTOUT_REASONS, created_at__gt=since
        ).values_list("value", flat=True)
    )
    if not emails:
        return {}
    newest: dict[str, str] = {}
    sent = (
        Message.objects.filter(thread__channel=channel, direction=Direction.OUT, sent_at__isnull=False)
        .filter(thread__recipient_email__in=emails)
        .order_by("sent_at", "pk")
        .values_list("thread__recipient_email", "thread__sender_route")
    )
    for email, route_key in sent:
        newest[email.lower()] = route_key  # the newest sent mail wins
    totals: dict[str, int] = defaultdict(int)
    for email in emails:
        if email in newest:
            totals[newest[email]] += 1
    return totals


def _rate(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 2) if whole else None


def metrics(raw: dict[str, int]) -> dict:
    """The counters plus delivered and the percent rates (null while nothing was sent)."""
    sent = raw["sent"]
    return {
        **raw,
        "delivered": max(sent - raw["hard_bounces"], 0),
        "hard_bounce_rate_pct": _rate(raw["hard_bounces"], sent),
        "soft_bounce_rate_pct": _rate(raw["soft_bounces"], sent),
        "optout_rate_pct": _rate(raw["optouts"], sent),
        "complaint_rate_pct": _rate(raw["complaints"], sent),
        "reply_rate_pct": _rate(raw["replies"], sent),
    }


def report(channel: Channel, now: datetime) -> dict:
    """Health of the channel (all routes together) and of each route, per window."""
    by_window = {name: counts(channel, now - span) for name, span in WINDOWS.items()}
    routes = {route.key: route for route in SenderRoute.objects.filter(channel=channel).order_by("key")}
    keys = sorted({*routes, *(key for found in by_window.values() for key in found)})
    empty = dict.fromkeys(COUNTERS, 0)

    def row(key: str) -> dict:
        route = routes.get(key)
        return {
            "route_key": key,
            "from_email": route.from_email if route else "",
            "is_active": route.is_active if route else None,
            "paused": bool(route and route.paused_at),
            "paused_at": route.paused_at if route else None,
            "pause_reason": route.pause_reason if route else "",
            "windows": {name: metrics(found.get(key, empty)) for name, found in by_window.items()},
        }

    totals = {
        name: metrics({c: sum(r[c] for r in found.values()) for c in COUNTERS}) for name, found in by_window.items()
    }
    return {"channel": {"windows": totals}, "routes": [row(key) for key in keys]}


def _breach(policy: HealthPolicy, raw: dict[str, int]) -> tuple[str, str] | None:
    """(trigger, readable reason) of the first threshold the counts exceed; None below the minimum sample."""
    sent = raw["sent"]
    if sent < policy.min_sample:
        return None
    checks = (
        (HealthTrigger.COMPLAINT, "complaints", raw["complaints"], policy.max_complaint_pct),
        (HealthTrigger.BOUNCE, "hard bounces", raw["hard_bounces"], policy.max_bounce_pct),
        (HealthTrigger.OPTOUT, "opt-outs", raw["optouts"], policy.max_optout_pct),
    )
    for trigger, label, count, limit in checks:
        if Decimal(count) * 100 / sent > Decimal(limit):
            rate = _rate(count, sent)
            return trigger, f"{label} {rate}% of {sent} sent in {policy.window_hours} h, limit {limit}%"
    return None


def evaluate(channel: Channel, now: datetime) -> list[str]:
    """Pause the routes that broke a threshold; the keys paused now. Does nothing while the policy is off or the
    channel is not live."""
    policy = HealthPolicy.objects.filter(channel=channel, enabled=True).first()
    if policy is None or channel.mode != ChannelMode.LIVE:
        return []
    since = now - timedelta(hours=policy.window_hours)
    found = counts(channel, since)
    paused = []
    for route in SenderRoute.objects.filter(channel=channel, is_active=True, paused_at__isnull=True):
        raw = found.get(route.key)
        if route.health_reset_at is not None and route.health_reset_at > since:
            raw = counts(channel, route.health_reset_at).get(route.key)
        breach = _breach(policy, raw) if raw else None
        if breach is not None and _pause(route, now, trigger=breach[0], reason=breach[1], raw=raw):
            paused.append(route.key)
    return paused


def _pause(route: SenderRoute, now: datetime, *, trigger: str, reason: str, raw: dict, user=None) -> bool:
    """False when the route was already paused (nothing logged, nobody notified twice)."""
    with transaction.atomic():
        changed = SenderRoute.objects.filter(pk=route.pk, paused_at__isnull=True).update(
            paused_at=now, pause_reason=reason[:255], modified_at=now
        )
        if not changed:
            return False
        RoutePauseLog.objects.create(
            channel=route.channel,
            route_key=route.key,
            action=RouteLogAction.PAUSE,
            trigger=trigger,
            reason=reason[:255],
            metrics=metrics(raw),
            user=user,
        )
    route.paused_at, route.pause_reason = now, reason[:255]
    logger.warning("communicator route %s of channel %s paused: %s", route.key, route.channel.idx, reason)
    if user is None:
        alert_service.notify_subject(
            route.channel,
            subject_ref=f"communicator.SenderRoute:{route.channel.idx}:{route.key}",
            severity="high",
            title=f"Sending paused on route {route.key}: {reason}",
        )
    return True


def pause_manually(route: SenderRoute, now: datetime, *, reason: str, user) -> SenderRoute:
    """Raises `RouteStateError` when the route is already paused."""
    if not _pause(route, now, trigger=HealthTrigger.MANUAL, reason=reason, raw=counts_of(route, now), user=user):
        raise RouteStateError(f"sender route {route.key} is already paused")
    return route


def resume(route: SenderRoute, now: datetime, *, reason: str, user) -> SenderRoute:
    """The only way back. The auto-pause then counts only events after `now`. Raises `RouteStateError` when the route
    is not paused."""
    with transaction.atomic():
        changed = SenderRoute.objects.filter(pk=route.pk, paused_at__isnull=False).update(
            paused_at=None, pause_reason="", health_reset_at=now, modified_at=now
        )
        if not changed:
            raise RouteStateError(f"sender route {route.key} is not paused")
        RoutePauseLog.objects.create(
            channel=route.channel,
            route_key=route.key,
            action=RouteLogAction.RESUME,
            trigger=HealthTrigger.MANUAL,
            reason=reason[:255],
            metrics=metrics(counts_of(route, now)),
            user=user,
        )
    route.refresh_from_db()
    return route


def counts_of(route: SenderRoute, now: datetime) -> dict[str, int]:
    since = now - timedelta(hours=get_policy(route.channel).window_hours)
    return counts(route.channel, since).get(route.key, dict.fromkeys(COUNTERS, 0))


def is_paused(message: Message) -> bool:
    """True when the route the message leaves by is paused, or is a gmail_api route whose account cannot send. An unusable route is not 'paused' (delivery fails it)."""
    from django_communicator.services import gmail_transport, sender_route_service

    try:
        route = sender_route_service.route_for(message)
    except sender_route_service.SenderRouteError:
        return False
    if route is None:
        return False
    return bool(route.paused_at) or gmail_transport.route_blocked(route, message.thread.channel)
