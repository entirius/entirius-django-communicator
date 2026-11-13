# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Beat `resume_paused_sequences`: a sequence paused until the recipient's return runs again once `resume_at` is
due, when nothing happened since — still paused, the thread not closed, no opt-out waiting for a person, the
address free to mail. A newer reply
already dropped `resume_at` (`sequence_service.stop_sequence`). A due state that is not resumed loses `resume_at`."""

from django.db import transaction

from django_communicator.enums import ReplyKind, SequenceStopReason, ThreadStatus
from django_communicator.models import Reply, Thread, ThreadSequenceState
from django_communicator.services import clock_service, inbound_service, sequence_service, suppression_service


def resume_due() -> int:
    """How many sequences were resumed."""
    states = ThreadSequenceState.objects.select_related("thread__channel").filter(
        stop_reason=SequenceStopReason.PAUSED, resume_at__isnull=False
    )
    due = [state for state in states if state.resume_at <= clock_service.now_for(state.thread.channel)]
    return sum(1 for state in due if _resume(state))


@transaction.atomic
def _resume(state: ThreadSequenceState) -> bool:
    thread = Thread.objects.select_for_update().select_related("channel").get(pk=state.thread_id)
    claimed = ThreadSequenceState.objects.filter(
        pk=state.pk, stop_reason=SequenceStopReason.PAUSED, resume_at=state.resume_at
    ).update(resume_at=None)
    if not claimed or thread.status == ThreadStatus.CLOSED:
        return False
    if _undecided_optout(thread) or suppression_service.can_mail(thread.channel, thread.recipient_email):
        return False
    sequence_service.resume_sequence(thread)
    inbound_service.reopen(thread)
    return True


def _undecided_optout(thread: Thread) -> bool:
    """A suspected opt-out waits for a person: nothing resumes the sequence before that."""
    return Reply.objects.filter(thread=thread, kind=ReplyKind.SUSPECTED_OPTOUT, optout_confirmed_at=None).exists()
