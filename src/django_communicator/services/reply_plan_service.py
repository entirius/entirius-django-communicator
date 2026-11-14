# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""What a reply of each class does: the plan per channel and class, and its application to a stored reply.

A `ReplyClassPlan` row per `(channel, intent)` overrides the code default. Nothing here suppresses, reminds or moves
a stage: a plan stops or pauses the thread's sequence, picks the alert severity and flags the reply for review. A
reply without a class gets what every reply got before classes existed: sequence stopped, high alert.
"""

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from django_communicator.enums import AlertSeverity, ReplyIntent, ReplySequenceAction, SequenceStopReason
from django_communicator.models import Channel, Reply, ReplyClassPlan, Thread, ThreadSequenceState
from django_communicator.services import alert_service, sequence_service
from django_communicator.services.exceptions import ClientError
from django_communicator.signals import REPLY_ALERTED, reply_received

UNCLASSIFIED = ""
PLAN_FIELDS = frozenset({"sequence", "notify", "needs_review"})
_STOP, _PAUSE, _UNTIL_RETURN = (
    ReplySequenceAction.STOP,
    ReplySequenceAction.PAUSE,
    ReplySequenceAction.PAUSE_UNTIL_RETURN,
)
_HIGH, _MEDIUM, _LOW = AlertSeverity.HIGH, AlertSeverity.MEDIUM, AlertSeverity.LOW


class ReplyPlanError(ClientError):
    """The requested plan is not allowed for this class."""


@dataclass(frozen=True)
class Plan:
    intent: str
    sequence: str
    notify: str
    needs_review: bool
    is_default: bool = True


DEFAULTS: dict[str, Plan] = {
    plan.intent: plan
    for plan in (
        Plan(ReplyIntent.INTERESTED, _STOP, _HIGH, False),
        Plan(ReplyIntent.NOT_NOW, _PAUSE, _MEDIUM, False),
        Plan(ReplyIntent.NOT_INTERESTED, _STOP, _MEDIUM, False),
        Plan(ReplyIntent.OUT_OF_OFFICE, _UNTIL_RETURN, _LOW, False),
        Plan(ReplyIntent.WRONG_PERSON, _STOP, _MEDIUM, True),
        Plan(ReplyIntent.UNSUBSCRIBE, _PAUSE, _MEDIUM, True),  # the suspected opt-out path: always a pause
        Plan(ReplyIntent.UNCLEAR, _STOP, _HIGH, True),
    )
}
UNCLASSIFIED_PLAN = Plan(UNCLASSIFIED, _STOP, _HIGH, False)
# A suspected opt-out raised by the phrase list, not by the class: what it did before classes existed.
PHRASE_OPTOUT_PLAN = Plan(UNCLASSIFIED, _PAUSE, _MEDIUM, False)


def _plan_of(row: ReplyClassPlan) -> Plan:
    return Plan(row.intent, row.sequence, row.notify, row.needs_review, is_default=False)


def plan_for(channel: Channel, intent: str) -> Plan:
    if intent not in DEFAULTS:
        return UNCLASSIFIED_PLAN
    row = ReplyClassPlan.objects.filter(channel=channel, intent=intent).first()
    return _plan_of(row) if row else DEFAULTS[intent]


def effective(channel: Channel) -> list[Plan]:
    """One plan per class, in the order of `ReplyIntent`."""
    rows = {row.intent: row for row in ReplyClassPlan.objects.filter(channel=channel)}
    return [_plan_of(rows[intent]) if intent in rows else DEFAULTS[intent] for intent in ReplyIntent.values]


def _validate(intent: str, fields: dict) -> None:
    if intent not in DEFAULTS:
        raise ReplyPlanError(f"unknown class {intent!r}")
    invalid = set(fields) - PLAN_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable on a reply class plan: {sorted(invalid)}")
    sequence = fields.get("sequence")
    if sequence == _UNTIL_RETURN and intent != ReplyIntent.OUT_OF_OFFICE:
        raise ReplyPlanError("pause_until_return is only for the out_of_office class")
    if intent == ReplyIntent.UNSUBSCRIBE and sequence != _PAUSE:
        raise ReplyPlanError("the unsubscribe class always pauses the sequence until a person decides")


def upsert(channel: Channel, intent: str, fields: dict) -> Plan:
    """The whole plan of one class is replaced (a PUT). Raises `ReplyPlanError` for a plan that is not allowed."""
    _validate(intent, fields)
    row, _ = ReplyClassPlan.objects.update_or_create(channel=channel, intent=intent, defaults=fields)
    return _plan_of(row)


def reset(channel: Channel, intent: str) -> Plan:
    """Back to the code default."""
    if intent not in DEFAULTS:
        raise ReplyPlanError(f"unknown class {intent!r}")
    ReplyClassPlan.objects.filter(channel=channel, intent=intent).delete()
    return DEFAULTS[intent]


def resume_moment(channel: Channel, return_date: date) -> datetime:
    """The day after the person is back, 00:00 in the channel's timezone."""
    return datetime.combine(return_date + timedelta(days=1), time.min, tzinfo=ZoneInfo(channel.timezone))


def apply(reply: Reply, plan: Plan) -> None:
    """Sequence, review flag and snapshot for a stored human reply (not a suspected opt-out); the alert is
    `announce`'s, after the commit."""
    thread = reply.thread
    resume_at = None
    if plan.sequence == _STOP:
        sequence_service.stop_sequence(thread, SequenceStopReason.REPLIED)
    else:
        sequence_service.pause_sequence(thread)
    if plan.sequence == _UNTIL_RETURN and reply.intent_return_date:
        resume_at = resume_moment(reply.channel, reply.intent_return_date)
        ThreadSequenceState.objects.filter(thread=thread, stop_reason=SequenceStopReason.PAUSED).update(
            resume_at=resume_at, modified_at=timezone.now()
        )
    record(reply, plan, resume_at=resume_at)


def record(reply: Reply, plan: Plan, *, resume_at: datetime | None = None) -> None:
    """`needs_review` and the `plan_applied` snapshot, on the row and on the instance receivers get."""
    snapshot = {**asdict(plan), "resume_at": resume_at.isoformat() if resume_at else None}
    reply.needs_review, reply.plan_applied = plan.needs_review, snapshot
    Reply.objects.filter(pk=reply.pk).update(needs_review=plan.needs_review, plan_applied=snapshot)


def reply_type_label(intent: str) -> str:
    """The words of a reply class for people ("Not now"); "" for a reply without a class."""
    return ReplyIntent(intent).label if intent in ReplyIntent.values else ""


NEEDS_REVIEW_WORDS = "Needs review"
POSSIBLE_OPTOUT_WORDS = "Possible opt-out"


def reply_title(sender: str, reply: Reply) -> str:
    """Every reply title: `Reply from <sender> — <class words>`; without a class `Reply from <sender> — Needs review`
    when the reply is flagged for a person, else `Reply from <sender>`."""
    label = reply_type_label(reply.intent) or (NEEDS_REVIEW_WORDS if reply.needs_review else "")
    return f"Reply from {sender} — {label}" if label else f"Reply from {sender}"


def optout_title(sender: str) -> str:
    """A suspected opt-out in the shape of every reply title: `Reply from <sender> — Possible opt-out`."""
    return f"Reply from {sender} — {POSSIBLE_OPTOUT_WORDS}"


def announce(reply: Reply, plan: Plan) -> None:
    """`reply_received` to every receiver (robust: a failing receiver is skipped), then communicator's own alert
    unless a receiver raised one and said so with `REPLY_ALERTED`: one bell entry per reply."""
    responses = reply_received.send_robust(sender=Thread, thread=reply.thread, reply=reply)
    if not any(response == REPLY_ALERTED for _, response in responses):
        notify(reply, plan, title=reply_title(reply.from_email, reply))


def notify(reply: Reply, plan: Plan, *, title: str) -> None:
    alert_service.notify_subject(reply.channel, subject_ref=reply.thread.subject_ref, severity=plan.notify, title=title)
