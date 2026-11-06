# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Due follow-ups of classified replies (T4): resume a sequence paused until a person's return, and remind a person.

Beat `process_reply_followups`. Both are best effort and conservative: a sequence resumes only when nothing happened
since (no newer reply, not suppressed, still paused, thread not closed), and a reminder is an alert, never a message.
"""

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from django_communicator.enums import ReplyKind, SequenceStopReason, ThreadStatus
from django_communicator.models import Reply
from django_communicator.services import (
    alert_service,
    clock_service,
    inbound_service,
    sequence_service,
    suppression_service,
)
from django_communicator.signals import reply_followup_due

logger = logging.getLogger(__name__)

HUMAN_KINDS = (ReplyKind.REPLY, ReplyKind.SUSPECTED_OPTOUT)


def run_due() -> dict[str, int]:
    """Resume and remind everything due on each channel's clock; returns `{"resumed": n, "reminded": n}`."""
    candidates = Reply.objects.filter(
        Q(resume_at__isnull=False, resumed_at=None) | Q(remind_at__isnull=False, reminded_at=None)
    ).select_related("channel", "thread")
    counts = {"resumed": 0, "reminded": 0}
    for reply in candidates:
        now = clock_service.now_for(reply.channel)
        for kind, due_at in (("resumed", reply.resume_at), ("reminded", reply.remind_at)):
            if due_at is None or due_at > now or getattr(reply, f"{kind}_at"):
                continue
            try:
                counts[kind] += int(_process(reply.pk, kind))
            except Exception:  # noqa: BLE001 — one bad row must not stop the beat
                logger.exception("communicator reply follow-up %s of reply %s failed", kind, reply.pk)
    return counts


@transaction.atomic
def _process(pk: int, kind: str) -> bool:
    """True when the action happened; the due row is closed either way so it is not retried forever."""
    reply = Reply.objects.select_for_update(of=("self",)).select_related("channel", "thread").get(pk=pk)
    if getattr(reply, f"{kind}_at") is not None:
        return False
    setattr(reply, f"{kind}_at", timezone.now())
    reply.save(update_fields=[f"{kind}_at", "modified_at"])
    if _superseded(reply):
        return False
    if kind == "resumed":
        return _resume(reply)
    return _remind(reply)


def _superseded(reply: Reply) -> bool:
    """A later human reply (or a pending opt-out) in the thread means the plan of this one is stale."""
    return Reply.objects.filter(thread=reply.thread, kind__in=HUMAN_KINDS, received_at__gt=reply.received_at).exists()


def _resume(reply: Reply) -> bool:
    thread = reply.thread
    if suppression_service.is_suppressed(reply.channel, thread.recipient_email):
        sequence_service.stop_sequence(thread, SequenceStopReason.SUPPRESSED)
        return False
    if thread.status == ThreadStatus.CLOSED:
        return False
    try:
        sequence_service.resume_sequence(thread)
    except sequence_service.SequenceError:
        return False  # a person stopped or finished it in the meantime
    inbound_service.reopen(thread)
    return True


def _remind(reply: Reply) -> bool:
    title = f"Reminder: follow up with {reply.from_email} ({reply.intent or 'reply'})"
    alert_service.notify_subject(reply.channel, subject_ref=reply.thread.subject_ref, severity="medium", title=title)
    thread = reply.thread
    transaction.on_commit(lambda: reply_followup_due.send(sender=Reply, thread=thread, reply=reply, kind="reminder"))
    return True
