# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Next steps per reply intent class and channel (T4): the plan and its application to a stored reply.

A `ReplyIntentAction` row per `(channel, intent)` overrides the code default. The defaults are safe: nothing is
suppressed, no stage moves here (leads owns stages), a sequence is stopped or paused — never left running — and an
unclassified reply (`intent == ""`: no toolbox, a failed call) gets exactly what every reply got before T4 (sequence
stopped, high alert) with nobody's decision made for them. Sequences stop on any reply unless the plan pauses them.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction

from django_communicator import settings as communicator_settings
from django_communicator.enums import (
    NotifySeverity,
    ReplyIntent,
    ReplySequenceAction,
    SequenceStopReason,
)
from django_communicator.models import Channel, Reply, ReplyIntentAction
from django_communicator.services import alert_service, clock_service, sequence_service
from django_communicator.services.exceptions import ClientError

MAX_REMIND_DAYS = 365
UNCLASSIFIED = ""


class ReplyActionError(ClientError):
    """The requested mapping is not allowed."""


@dataclass(frozen=True)
class Plan:
    intent: str
    sequence: str
    notify: str
    remind_after_days: int | None
    needs_review: bool
    min_confidence: float | None
    auto_suppress: bool
    is_default: bool = True


def _default(intent, sequence, notify, remind=None, review=False) -> Plan:
    return Plan(intent, sequence, notify, remind, review, None, False)


DEFAULTS: dict[str, Plan] = {
    ReplyIntent.INTERESTED: _default(ReplyIntent.INTERESTED, ReplySequenceAction.STOP, NotifySeverity.HIGH),
    ReplyIntent.NOT_NOW: _default(ReplyIntent.NOT_NOW, ReplySequenceAction.PAUSE, NotifySeverity.MEDIUM, remind=30),
    ReplyIntent.NOT_INTERESTED: _default(ReplyIntent.NOT_INTERESTED, ReplySequenceAction.STOP, NotifySeverity.MEDIUM),
    ReplyIntent.OUT_OF_OFFICE: _default(
        ReplyIntent.OUT_OF_OFFICE, ReplySequenceAction.PAUSE_UNTIL_RETURN, NotifySeverity.LOW, remind=7
    ),
    ReplyIntent.WRONG_PERSON: _default(
        ReplyIntent.WRONG_PERSON, ReplySequenceAction.STOP, NotifySeverity.MEDIUM, review=True
    ),
    ReplyIntent.UNSUBSCRIBE: _default(ReplyIntent.UNSUBSCRIBE, ReplySequenceAction.STOP, NotifySeverity.MEDIUM),
    ReplyIntent.UNCLEAR: _default(ReplyIntent.UNCLEAR, ReplySequenceAction.STOP, NotifySeverity.HIGH, review=True),
}
# No class at all: what every reply did before T4.
UNCLASSIFIED_PLAN = _default(UNCLASSIFIED, ReplySequenceAction.STOP, NotifySeverity.HIGH)


def _plan_of(row: ReplyIntentAction) -> Plan:
    return Plan(
        intent=row.intent,
        sequence=row.sequence,
        notify=row.notify,
        remind_after_days=row.remind_after_days,
        needs_review=row.needs_review,
        min_confidence=row.min_confidence,
        auto_suppress=row.auto_suppress,
        is_default=False,
    )


def plan_for(channel: Channel, intent: str) -> Plan:
    if intent not in DEFAULTS:
        return UNCLASSIFIED_PLAN
    row = ReplyIntentAction.objects.filter(channel=channel, intent=intent).first()
    return _plan_of(row) if row else DEFAULTS[intent]


def effective(channel: Channel) -> list[Plan]:
    """One plan per intent class, in the order of `ReplyIntent`."""
    rows = {row.intent: row for row in ReplyIntentAction.objects.filter(channel=channel)}
    return [_plan_of(rows[intent]) if intent in rows else DEFAULTS[intent] for intent in ReplyIntent.values]


def min_confidence(channel: Channel, intent: str) -> float:
    plan = plan_for(channel, intent)
    if plan.min_confidence is not None:
        return plan.min_confidence
    return communicator_settings.COMMUNICATOR_REPLY_INTENT_MIN_CONFIDENCE


def validate(intent: str, fields: dict) -> None:
    """Raises `ReplyActionError` for a mapping that makes no sense."""
    if intent not in DEFAULTS:
        raise ReplyActionError(f"unknown intent {intent!r}")
    if fields.get("auto_suppress") and intent != ReplyIntent.UNSUBSCRIBE:
        raise ReplyActionError("auto_suppress is only for the unsubscribe class")
    if fields.get("sequence") == ReplySequenceAction.PAUSE_UNTIL_RETURN and intent != ReplyIntent.OUT_OF_OFFICE:
        raise ReplyActionError("pause_until_return is only for the out_of_office class")
    days = fields.get("remind_after_days")
    if days is not None and not 0 < days <= MAX_REMIND_DAYS:
        raise ReplyActionError(f"remind_after_days must be 1..{MAX_REMIND_DAYS}")
    confidence = fields.get("min_confidence")
    if confidence is not None and not 0 <= confidence <= 1:
        raise ReplyActionError("min_confidence must be between 0 and 1")


@transaction.atomic
def upsert(channel: Channel, intent: str, fields: dict) -> Plan:
    """The whole mapping of one class is replaced (a PUT): omitted fields return to the model defaults."""
    validate(intent, fields)
    row, _ = ReplyIntentAction.objects.update_or_create(channel=channel, intent=intent, defaults=fields)
    return _plan_of(row)


def reset(channel: Channel, intent: str) -> Plan:
    """Back to the code default."""
    if intent not in DEFAULTS:
        raise ReplyActionError(f"unknown intent {intent!r}")
    ReplyIntentAction.objects.filter(channel=channel, intent=intent).delete()
    return DEFAULTS[intent]


def resume_moment(channel: Channel, return_date) -> datetime:
    """The day after the person is back, 00:00 in the channel's timezone."""
    zone = ZoneInfo(channel.timezone)
    return datetime.combine(return_date + timedelta(days=1), time.min, tzinfo=zone)


def apply(reply: Reply, plan: Plan) -> None:
    """Sequence, reminder, review flag, alert and the stored snapshot for a human reply (not an opt-out
    suspicion). The thread status is already `replied`."""
    thread, channel = reply.thread, reply.channel
    now = clock_service.now_for(channel)
    resume_at = remind_at = None
    if plan.sequence == ReplySequenceAction.STOP:
        sequence_service.stop_sequence(thread, SequenceStopReason.REPLIED)
    else:
        sequence_service.pause_sequence(thread)
        if plan.sequence == ReplySequenceAction.PAUSE_UNTIL_RETURN and reply.intent_return_date:
            resume_at = resume_moment(channel, reply.intent_return_date)
    if plan.remind_after_days and resume_at is None:
        remind_at = now + timedelta(days=plan.remind_after_days)
    snapshot = {
        **asdict(plan),
        "resume_at": resume_at.isoformat() if resume_at else None,
        "remind_at": remind_at.isoformat() if remind_at else None,
    }
    Reply.objects.filter(pk=reply.pk).update(
        needs_review=plan.needs_review, action_applied=snapshot, resume_at=resume_at, remind_at=remind_at
    )
    reply.needs_review, reply.action_applied, reply.resume_at, reply.remind_at = (
        plan.needs_review,
        snapshot,
        resume_at,
        remind_at,
    )
    notify(reply, plan)


def notify(reply: Reply, plan: Plan) -> None:
    if plan.notify == NotifySeverity.NONE:
        return
    title = f"Reply from {reply.from_email}"
    if reply.intent:
        title += f" ({reply.intent})"
    alert_service.notify_subject(reply.channel, subject_ref=reply.thread.subject_ref, severity=plan.notify, title=title)
