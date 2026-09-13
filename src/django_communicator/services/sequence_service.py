# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Follow-up sequences: start, stop, pause, schedule the next follow-up from the text pool.

`days_after_previous` counts from the delivery of the previous message: the state gets its next due date when a
message of the thread is delivered (`on_delivered`), and none while a scheduled follow-up waits to be sent.
"""

import logging
import random
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from django_communicator.enums import Direction, MessageStatus, SequenceStopReason, ThreadStatus
from django_communicator.models import Message, Sequence, TextPool, Thread, ThreadPoolUsage, ThreadSequenceState
from django_communicator.services import clock_service
from django_communicator.services.communicate_service import (
    LegalFooterRequiredError,
    RecipientData,
    ThreadMismatchError,
    communicate,
)
from django_communicator.signals import sequence_finished

logger = logging.getLogger(__name__)

DELIVERED = (MessageStatus.SENT, MessageStatus.WOULD_SEND)


class SequenceError(Exception):
    pass


def _last_delivered(thread: Thread) -> Message | None:
    messages = Message.objects.filter(thread=thread, direction=Direction.OUT, status__in=DELIVERED)
    return messages.exclude(sent_at=None).order_by("-sent_at", "-pk").first()


def start_sequence(thread: Thread, sequence: Sequence) -> ThreadSequenceState:
    """Raises `SequenceError` for a sequence of another channel or without steps."""
    first = sequence.steps.order_by("number").first()
    if sequence.channel_id != thread.channel_id or first is None:
        raise SequenceError(f"sequence {sequence.key} cannot run in thread {thread.pk}")
    last = _last_delivered(thread)
    due = last.sent_at + timedelta(days=first.days_after_previous) if last else None
    fields = {"sequence": sequence, "step": 0, "next_due_at": due, "stopped_at": None, "stop_reason": ""}
    state, _ = ThreadSequenceState.objects.update_or_create(thread=thread, defaults=fields)
    return state


def stop_sequence(thread: Thread, reason: str) -> None:
    ThreadSequenceState.objects.filter(thread=thread, stopped_at=None).update(
        stopped_at=timezone.now(), stop_reason=reason, next_due_at=None
    )


def pause_sequence(thread: Thread) -> None:
    stop_sequence(thread, SequenceStopReason.PAUSED)


def on_delivered(message: Message) -> None:
    """Next due date from this delivery, or `finished` (+ `sequence_finished` on commit) after the last step."""
    state = ThreadSequenceState.objects.filter(thread_id=message.thread_id, stopped_at=None).first()
    if state is None:
        return
    steps = list(state.sequence.steps.order_by("number"))
    if state.step >= len(steps):
        stop_sequence(message.thread, SequenceStopReason.FINISHED)
        transaction.on_commit(lambda: sequence_finished.send(sender=Thread, thread=message.thread))
        return
    state.next_due_at = message.sent_at + timedelta(days=steps[state.step].days_after_previous)
    state.save(update_fields=["next_due_at", "modified_at"])


def run_follow_ups(rng: random.Random | None = None) -> int:
    """Schedule every due follow-up of open threads; returns how many were created."""
    states = ThreadSequenceState.objects.select_related("thread__channel", "sequence").filter(
        stopped_at=None, next_due_at__isnull=False, thread__status=ThreadStatus.OPEN, sequence__is_active=True
    )
    rng = rng or random.Random()  # noqa: S311 — text choice, not security
    due = [state for state in states if state.next_due_at <= clock_service.now_for(state.thread.channel)]
    return sum(1 for state in due if _schedule_logged(state, rng) is not None)


def _schedule_logged(state: ThreadSequenceState, rng: random.Random) -> Message | None:
    try:
        return schedule_follow_up(state, rng)
    except (LegalFooterRequiredError, ThreadMismatchError):
        logger.warning("communicator follow-up of thread %s not created", state.thread_id, exc_info=True)
        return None


@transaction.atomic
def schedule_follow_up(state: ThreadSequenceState, rng: random.Random) -> Message | None:
    steps = list(state.sequence.steps.order_by("number"))
    text = pick_text(state, rng)
    if text is None or state.step >= len(steps):
        return None
    thread, step = state.thread, steps[state.step]
    message = communicate(
        channel_idx=thread.channel.idx,
        template_key=step.template_key,
        recipient=_recipient(thread),
        context={**_previous_context(thread), "body": text.body},
        subject_ref=thread.subject_ref,
        requires_review=False,
        thread=thread,
    )
    ThreadPoolUsage.objects.get_or_create(thread=thread, text=text)
    ThreadSequenceState.objects.filter(pk=state.pk).update(step=state.step + 1, next_due_at=None)
    return message


def pick_text(state: ThreadSequenceState, rng: random.Random) -> TextPool | None:
    """A random unused active text; the whole pool again with a warning once every text was used (C-27)."""
    texts = list(TextPool.objects.filter(sequence=state.sequence, is_active=True).order_by("pk"))
    if not texts:
        logger.warning("communicator sequence %s has no active texts", state.sequence.key)
        return None
    used = set(ThreadPoolUsage.objects.filter(thread=state.thread).values_list("text_id", flat=True))
    fresh = [text for text in texts if text.pk not in used]
    if not fresh:
        logger.warning("communicator thread %s used the whole pool of %s", state.thread_id, state.sequence.key)
    return rng.choice(fresh or texts)


def _previous_context(thread: Thread) -> dict:
    last = Message.objects.filter(thread=thread, direction=Direction.OUT).order_by("-created_at", "-pk").first()
    return dict(last.render_context) if last else {}


def _recipient(thread: Thread) -> RecipientData:
    last = Message.objects.filter(thread=thread, direction=Direction.OUT).order_by("-created_at", "-pk").first()
    first_name, _, last_name = thread.recipient_name.partition(" ")
    language = thread.recipient_language.iso2.lower() if thread.recipient_language else ""
    footer = last.legal_footer if last else ""
    return RecipientData(
        email=thread.recipient_email, first_name=first_name, last_name=last_name, language=language, legal_footer=footer
    )
