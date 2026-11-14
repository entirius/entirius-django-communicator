# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`compose()` — one message a person wrote by hand: no template, no AI, approved by its author.

It rides the send path of every other message (mode, policy window, caps, mailbox hold, delivery checks) and ends the
automation of its thread: the thread's sequence stops with reason `manual`, and a delivered hand-written message never
arms, re-arms or autostarts a sequence (`sequence_service.on_delivered`).
"""

from django.db import transaction
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import MessageStatus, SequenceStopReason, ThreadStatus
from django_communicator.models import Message, Reply, Thread
from django_communicator.services import (
    channel_service,
    clock_service,
    mailbox_service,
    message_service,
    sequence_service,
)
from django_communicator.services.communicate_service import (
    LegalFooterRequiredError,
    RecipientData,
    ThreadMismatchError,
    check_thread,
    label_thread,
    mail_refusal,
    thread_for,
)
from django_communicator.services.exceptions import ClientError
from django_communicator.signals import message_approved

SUBJECT_MAX_CHARS = 255
REUSABLE_THREADS = (ThreadStatus.OPEN, ThreadStatus.REPLIED)


class ComposeError(ClientError):
    """The subject or the body is blank or over its limit — no Message is created."""


def compose(
    *,
    channel_idx: str,
    recipient: RecipientData,
    subject: str,
    body_text: str,
    subject_ref: str,
    author,
    mailbox_user_id: int | None = None,
    thread: Thread | None = None,
    in_reply_to: Reply | None = None,
    subject_label: str = "",
) -> Message:
    """Can mail (a refusal → `suppressed` message) → legal footer → thread → `approved` message, due now.

    The thread: the given one (or the reply's), else the newest open or replied thread of (channel, subject_ref,
    email), else a new one. Raises `Channel.DoesNotExist`, `ComposeError`, `ThreadMismatchError` and
    `LegalFooterRequiredError` — nothing is created then. A non-empty `subject_label` is stored on the thread."""
    channel = channel_service.get_channel(channel_idx)
    _check_text(subject, body_text)
    if thread := _reply_thread(thread, in_reply_to):
        check_thread(thread, channel, recipient, subject_ref)
    refusal = mail_refusal(channel, recipient.email)
    if refusal is None and not recipient.legal_footer.strip():
        raise LegalFooterRequiredError("a hand-written message requires a legal footer")
    thread = thread or thread_for(channel, recipient, subject_ref, REUSABLE_THREADS)
    mailbox_service.assign(thread, mailbox_user_id)
    label_thread(thread, subject_label)
    fields = {"subject": subject, "body_text": body_text, "legal_footer": recipient.legal_footer}
    fields.update(
        created_by=author, composed=True, edited_by_human=True, requires_review=False, in_reply_to=in_reply_to
    )
    if refusal:
        return message_service.create_message(
            thread=thread, status=MessageStatus.SUPPRESSED, failure_detail=refusal, **fields
        )
    return _approved(thread, fields)


def _check_text(subject: str, body_text: str) -> None:
    limit = communicator_settings.COMMUNICATOR_COMPOSE_MAX_CHARS
    if not subject.strip() or len(subject) > SUBJECT_MAX_CHARS:
        raise ComposeError(f"the subject must have 1 to {SUBJECT_MAX_CHARS} characters")
    if not body_text.strip() or len(body_text) > limit:
        raise ComposeError(f"the body must have 1 to {limit} characters")


def _reply_thread(thread: Thread | None, in_reply_to: Reply | None) -> Thread | None:
    """The given thread, else the thread of the reply; a reply of another thread raises `ThreadMismatchError`."""
    if in_reply_to is None:
        return thread
    if thread is not None and in_reply_to.thread_id != thread.pk:
        raise ThreadMismatchError(f"reply {in_reply_to.pk} belongs to another thread")
    return thread or in_reply_to.thread


def _approved(thread: Thread, fields: dict) -> Message:
    """Approved by its author and due at the channel's now; a replied thread is open again, its sequence stopped."""
    due = clock_service.now_for(thread.channel)
    with transaction.atomic():
        message = message_service.create_message(
            thread=thread,
            status=MessageStatus.APPROVED,
            reviewed_by=fields["created_by"],
            reviewed_at=timezone.now(),
            scheduled_at=due,
            **fields,
        )
        reopened = Thread.objects.filter(pk=thread.pk, status=ThreadStatus.REPLIED)
        if reopened.update(status=ThreadStatus.OPEN, modified_at=timezone.now()):
            thread.status = ThreadStatus.OPEN
        sequence_service.stop_sequence(thread, SequenceStopReason.MANUAL)
        transaction.on_commit(lambda: message_approved.send(sender=Message, message=message))
    return message
