# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Sending health per mailbox and per channel, and the pause of a mailbox (automatic or by a person).

Counts are events inside a window, attributed by `Thread.mailbox`: mails delivered (`sent_at`; a delivery without
SMTP, `would_send`, counts too), hard bounces, soft bounces (a DSN for a message that waits for its retry),
complaints, human replies and opt-outs (a confirmed opt-out or an unsubscribe link, attributed to the mailbox of the
recipient's newest sent mail). Rates are percent of `sent`.

The auto-pause is off until the channel's `HealthPolicy` is enabled; it judges live channels only (a sandbox bounce
says nothing about real recipients), pauses one mailbox and never resumes it: only a person does, and the policy
then counts newer events only.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, QuerySet

from django_communicator.enums import (
    ChannelMode,
    Direction,
    FailureCode,
    HealthTrigger,
    MessageStatus,
    PauseAction,
    ReplyKind,
    SuppressionKind,
    SuppressionSource,
)
from django_communicator.models import Channel, HealthPolicy, Mailbox, MailboxPauseLog, Message, Reply, Suppression
from django_communicator.services import alert_service, mailbox_service
from django_communicator.services.exceptions import ClientError

logger = logging.getLogger(__name__)

COUNTERS = ("sent", "hard_bounces", "soft_bounces", "complaints", "optouts", "replies")
WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}
POLICY_FIELDS = frozenset(
    {"enabled", "window_hours", "min_sample", "max_bounce_pct", "max_optout_pct", "max_complaint_pct"}
)
_REPLY_COUNTERS = {
    ReplyKind.BOUNCE_HARD: "hard_bounces",
    ReplyKind.BOUNCE_SOFT: "soft_bounces",
    ReplyKind.COMPLAINT: "complaints",
    ReplyKind.REPLY: "replies",
}

OPTOUT_SOURCES = (SuppressionSource.OPTOUT_CONFIRMED, SuppressionSource.UNSUBSCRIBE_LINK)

Counts = dict[int | None, dict[str, int]]


class MailboxStateError(ClientError):
    """The mailbox is already in the state the request asks for."""


def get_policy(channel: Channel) -> HealthPolicy:
    """The channel's policy; an unsaved default (disabled) when none was stored."""
    return HealthPolicy.objects.filter(channel=channel).first() or HealthPolicy(channel=channel)


def save_policy(channel: Channel, fields: dict) -> HealthPolicy:
    invalid = set(fields) - POLICY_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable on a health policy: {sorted(invalid)}")
    policy, _ = HealthPolicy.objects.update_or_create(channel=channel, defaults=fields)
    return policy


def counts(channel: Channel, since: datetime) -> Counts:
    """{mailbox id: {counter: n}} for events after `since`; the key is None for mail without a mailbox."""
    result: Counts = defaultdict(lambda: dict.fromkeys(COUNTERS, 0))
    for row in _sent(channel).filter(sent_at__gt=since).values("thread__mailbox").annotate(n=Count("pk")):
        result[row["thread__mailbox"]]["sent"] += row["n"]
    for row in _replies(channel, since).values("kind", "thread__mailbox").annotate(n=Count("pk")):
        result[row["thread__mailbox"]][_REPLY_COUNTERS[row["kind"]]] += row["n"]
    for mailbox_id, n in _optouts(channel, since).items():
        result[mailbox_id]["optouts"] += n
    return result


def _sent(channel: Channel) -> QuerySet[Message]:
    """The channel's deliveries: what `sent` of a window counts."""
    delivered = Q(status__in=(MessageStatus.SENT, MessageStatus.WOULD_SEND))
    sent = delivered | Q(status=MessageStatus.FAILED, failure_code=FailureCode.BOUNCE)
    return Message.objects.filter(sent, thread__channel=channel, direction=Direction.OUT)


def _sent_today(channel: Channel, now: datetime) -> dict[int | None, int]:
    """{mailbox id: deliveries} of the channel's current day (`now` on the channel clock) — one query."""
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = _sent(channel).filter(sent_at__gte=start, sent_at__lt=start + timedelta(days=1))
    return dict(today.values("thread__mailbox").annotate(n=Count("pk")).values_list("thread__mailbox", "n"))


def _replies(channel: Channel, since: datetime) -> QuerySet[Reply]:
    soft = Q(kind=ReplyKind.BOUNCE_SOFT, message__bounce_retry_at__isnull=False)
    kinds = Q(kind__in=[ReplyKind.BOUNCE_HARD, ReplyKind.COMPLAINT, ReplyKind.REPLY]) | soft
    return Reply.objects.filter(kinds, channel=channel, received_at__gt=since)


def _optouts(channel: Channel, since: datetime) -> dict[int | None, int]:
    """Opt-out suppressions of the window (the channel's rows and global ones), each on the mailbox of the
    address's newest sent mail in the channel."""
    rows = Suppression.objects.filter(
        Q(channel=channel) | Q(channel=None),
        kind=SuppressionKind.EMAIL,
        source__in=OPTOUT_SOURCES,
        modified_at__gt=since,
    )
    emails = list(rows.values_list("value", flat=True))
    if not emails:
        return {}
    sent = Message.objects.filter(
        thread__channel=channel, thread__recipient_email__in=emails, direction=Direction.OUT, sent_at__isnull=False
    ).order_by("sent_at", "pk")
    newest = dict(sent.values_list("thread__recipient_email", "thread__mailbox"))  # the newest sent mail wins
    totals: dict[int | None, int] = defaultdict(int)
    for email in emails:
        if email in newest:
            totals[newest[email]] += 1
    return totals


def _rate(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 2) if whole else None


def metrics(raw: dict[str, int]) -> dict:
    """The counters plus the percent rates (null while nothing was sent)."""
    sent = raw["sent"]
    rates = {f"{name}_pct": _rate(raw[name], sent) for name in COUNTERS if name != "sent"}
    return {**raw, **rates}


def report(channel: Channel, now: datetime) -> dict:
    """Health of the channel (every mailbox and the channel SMTP entry together) and of each mailbox, per window;
    `now` is on the channel clock."""
    by_window = {name: counts(channel, now - span) for name, span in WINDOWS.items()}
    totals = {
        name: metrics({c: sum(r[c] for r in found.values()) for c in COUNTERS}) for name, found in by_window.items()
    }
    mailboxes = mailbox_service.list_mailboxes(channel)
    today = _sent_today(channel, now)
    rows = [{**_row(m, by_window, now), "sent_today": today.get(m.pk, 0)} for m in mailboxes]
    return {"channel": {"windows": totals}, "mailboxes": rows}


def _row(mailbox: Mailbox, by_window: dict[str, Counts], now: datetime) -> dict:
    empty = dict.fromkeys(COUNTERS, 0)
    return {
        "mailbox": mailbox,
        "state": mailbox_service.state(mailbox),
        "cap_today": mailbox_service.effective_cap(mailbox, now.date()),
        "windows": {name: metrics(found.get(mailbox.pk, empty)) for name, found in by_window.items()},
    }


def evaluate(channel: Channel, now: datetime) -> list[int]:
    """Pause the mailboxes over a threshold; the ids paused now. Nothing while the policy is off or the channel is
    not live."""
    policy = HealthPolicy.objects.filter(channel=channel, enabled=True).first()
    if policy is None or channel.mode != ChannelMode.LIVE:
        return []
    window_start = now - timedelta(hours=policy.window_hours)
    found = counts(channel, window_start)
    paused = []
    for mailbox in Mailbox.objects.filter(channel=channel, paused_at__isnull=True).select_related("channel"):
        raw = found.get(mailbox.pk)
        if mailbox.health_reset_at is not None and mailbox.health_reset_at > window_start:
            raw = counts(channel, mailbox.health_reset_at).get(mailbox.pk)
        breach = _breach(policy, raw) if raw else None
        if breach is not None and _pause(mailbox, now, trigger=breach[0], reason=breach[1], raw=raw):
            paused.append(mailbox.pk)
    return paused


def _breach(policy: HealthPolicy, raw: dict[str, int]) -> tuple[str, str] | None:
    """(trigger, readable reason) of the first limit the counts exceed; None below the minimum sample."""
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
            return trigger, f"{label} {_rate(count, sent)}% of {sent} sent in {policy.window_hours} h, limit {limit}%"
    return None


def _pause(mailbox: Mailbox, now: datetime, *, trigger: str, reason: str, raw: dict, user=None) -> bool:
    """False when the mailbox was already paused (nothing logged, nobody told twice)."""
    reason = reason[:255]
    with transaction.atomic():
        changed = Mailbox.objects.filter(pk=mailbox.pk, paused_at__isnull=True).update(
            paused_at=now, pause_reason=reason, modified_at=now
        )
        if not changed:
            return False
        _log(mailbox, PauseAction.PAUSE, trigger, reason, raw, user)
    mailbox.paused_at, mailbox.pause_reason = now, reason
    logger.warning("communicator mailbox %s of channel %s paused: %s", mailbox.pk, mailbox.channel.idx, reason)
    if user is None:
        alert_service.notify_subject(
            mailbox.channel,
            subject_ref=f"communicator.Mailbox:{mailbox.pk}",
            severity="high",
            title=f"Sending paused on mailbox {mailbox.address}: {reason}",
        )
    return True


def _log(mailbox: Mailbox, action: str, trigger: str, reason: str, raw: dict, user) -> None:
    MailboxPauseLog.objects.create(
        channel_id=mailbox.channel_id,
        mailbox=mailbox,
        action=action,
        trigger=trigger,
        reason=reason,
        metrics=metrics(raw),
        user=user,
    )


def pause_manually(mailbox: Mailbox, now: datetime, *, reason: str, user) -> Mailbox:
    """Raises `MailboxStateError` when the mailbox is already paused."""
    raw = counts_of(mailbox, now)
    if not _pause(mailbox, now, trigger=HealthTrigger.MANUAL, reason=reason, raw=raw, user=user):
        raise MailboxStateError(f"mailbox {mailbox.address} is already paused")
    return mailbox


def resume(mailbox: Mailbox, now: datetime, *, reason: str, user) -> Mailbox:
    """The only way back; the auto-pause then counts events after `now`. Raises `MailboxStateError` when the mailbox
    is not paused."""
    with transaction.atomic():
        changed = Mailbox.objects.filter(pk=mailbox.pk, paused_at__isnull=False).update(
            paused_at=None, pause_reason="", health_reset_at=now, modified_at=now
        )
        if not changed:
            raise MailboxStateError(f"mailbox {mailbox.address} is not paused")
        _log(mailbox, PauseAction.RESUME, HealthTrigger.MANUAL, reason[:255], counts_of(mailbox, now), user)
    mailbox.refresh_from_db()
    return mailbox


def counts_of(mailbox: Mailbox, now: datetime) -> dict[str, int]:
    since = now - timedelta(hours=get_policy(mailbox.channel).window_hours)
    return counts(mailbox.channel, since).get(mailbox.pk, dict.fromkeys(COUNTERS, 0))


def list_logs(channel: Channel) -> QuerySet[MailboxPauseLog]:
    return (
        MailboxPauseLog.objects.filter(channel=channel).select_related("mailbox", "user").order_by("-created_at", "-pk")
    )
