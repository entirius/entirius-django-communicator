# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A human decides a suspected opt-out: confirm (suppression of the thread recipient + `optout_confirmed`) or
dismiss (a plain reply, the sequence stays paused until `resume`)."""

import functools
import logging
from types import ModuleType

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from django_communicator.enums import ReplyKind, SequenceStopReason
from django_communicator.models import Reply, Thread, ThreadSequenceState
from django_communicator.services import inbound_service, sequence_service, suppression_service
from django_communicator.signals import optout_confirmed, reply_received

logger = logging.getLogger(__name__)

OBJECTION_RETRY_COUNTDOWN_S = 60


class OptoutStateError(Exception):
    """The reply is not an undecided suspected opt-out, or the thread cannot resume."""


@transaction.atomic
def confirm(reply: Reply, *, user) -> Reply:
    reply = _locked_suspicion(reply)
    thread = reply.thread
    email = thread.recipient_email
    suppression_service.suppress_email(thread.channel, email, reason="optout confirmed", user=user)
    sequence_service.stop_sequence(thread, SequenceStopReason.OPTOUT)
    reply.optout_confirmed_at, reply.optout_confirmed_by = timezone.now(), user
    reply.save(update_fields=["optout_confirmed_at", "optout_confirmed_by", "modified_at"])
    channel_idx = thread.channel.idx
    transaction.on_commit(
        lambda: optout_confirmed.send(
            sender=Reply, subject_ref=thread.subject_ref, email=email, channel_idx=channel_idx
        )
    )
    transaction.on_commit(lambda: _record_objection(channel_idx, email))
    return reply


@transaction.atomic
def dismiss(reply: Reply) -> Reply:
    """Not an opt-out after all: the reply path without a notification; the sequence stays paused."""
    reply = _locked_suspicion(reply)
    reply.kind = ReplyKind.REPLY
    reply.save(update_fields=["kind", "modified_at"])
    inbound_service.mark_message_replied(reply)
    transaction.on_commit(lambda: reply_received.send(sender=Thread, thread=reply.thread, reply=reply))
    return reply


@transaction.atomic
def resume(thread: Thread) -> ThreadSequenceState:
    """After a dismissed opt-out: sequence running again, thread `open`. Raises `OptoutStateError` while an
    opt-out is undecided or when the sequence is not paused."""
    thread = Thread.objects.select_for_update().select_related("channel").get(pk=thread.pk)
    if Reply.objects.filter(thread=thread, kind=ReplyKind.SUSPECTED_OPTOUT, optout_confirmed_at=None).exists():
        raise OptoutStateError(f"thread {thread.pk} has an undecided opt-out")
    try:
        state = sequence_service.resume_sequence(thread)
    except sequence_service.SequenceError as error:
        raise OptoutStateError(str(error)) from None
    inbound_service.reopen(thread)
    return state


def _locked_suspicion(reply: Reply) -> Reply:
    locked = Reply.objects.select_for_update().select_related("thread__channel").get(pk=reply.pk)
    if locked.kind != ReplyKind.SUSPECTED_OPTOUT or locked.optout_confirmed_at is not None:
        raise OptoutStateError(f"reply {reply.pk} is not an undecided suspected opt-out")
    return locked


def _record_objection(channel_idx: str, email: str) -> None:
    """Runs after the confirm committed — any failure is logged by class and retried by a task, never raised."""
    try:
        record_objection_now(channel_idx, email)
    except Exception as error:  # noqa: BLE001 — the confirm already committed; a 500 would invite a 409 retry
        logger.warning("communicator objection in channel %s not recorded: %s", channel_idx, type(error).__name__)
        _schedule_objection_retry(channel_idx, email)


def _schedule_objection_retry(channel_idx: str, email: str) -> None:
    from django_communicator.tasks import record_objection

    try:
        record_objection.apply_async((channel_idx, email), countdown=OBJECTION_RETRY_COUNTDOWN_S)
    except Exception as error:  # noqa: BLE001 — broker down: the objection stays unrecorded, logged
        logger.error("communicator objection retry in channel %s not queued: %s", channel_idx, type(error).__name__)


def record_objection_now(channel_idx: str, email: str) -> None:
    objection_service = _objection_service()
    if objection_service is None:
        return
    try:
        objection_service.record_objection(
            channel_idx=channel_idx, email=email, source="communicator", reason="reply opt-out"
        )
    except ObjectDoesNotExist:
        logger.warning("django_agreements has no channel %s — objection not recorded", channel_idx)


@functools.cache
def _objection_service() -> ModuleType | None:
    """Soft dependency: `django_agreements` records the objection when it is installed."""
    try:
        from django_agreements.services import objection_service
    except (ImportError, RuntimeError):
        return None
    return objection_service
