# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Review queue of a channel: list, next, accept, rewrite, manual edit, skip. Status changes via `message_service`."""

from django.db import transaction
from django.db.models import QuerySet
from django_utils.toolbox import ToolboxError

from django_communicator import settings as communicator_settings
from django_communicator.enums import MessageStatus
from django_communicator.models import Channel, Message
from django_communicator.services import drafting_service, message_service, sequence_service
from django_communicator.services.drafting_service import DraftOutputError
from django_communicator.services.send_service import initial_slot
from django_communicator.signals import company_skipped, message_approved

REWRITE_TAG = "communicator.rewrite"


class ReviewError(Exception):
    """The requested review action does not apply to this message."""


class RewriteLimitReachedError(ReviewError):
    pass


def list_messages(channel: Channel, *, status: str = MessageStatus.REVIEW_REQUIRED) -> QuerySet[Message]:
    """Oldest first, with thread, language and template loaded in the same query."""
    return (
        Message.objects.select_related("thread__recipient_language", "template_version__template")
        .filter(thread__channel=channel, status=status)
        .order_by("created_at", "pk")
    )


def next_for_review(channel: Channel) -> Message | None:
    return list_messages(channel).first()


def get_message(channel: Channel, pk: int) -> Message:
    """Raises `Message.DoesNotExist` when the message is not in this channel."""
    return Message.objects.select_related("thread__recipient_language", "template_version__template").get(
        thread__channel=channel, pk=pk
    )


def accept(message: Message, *, user) -> Message:
    """`approved` with reviewer, timestamp and `scheduled_at` = next policy slot (none without a policy).

    Signals once, on commit.
    """
    with transaction.atomic():
        slot = initial_slot(message.thread.channel)
        message_service.transition(message, MessageStatus.APPROVED, user=user, fields={"scheduled_at": slot})
        transaction.on_commit(lambda: message_approved.send(sender=Message, message=message))
    return message


def skip(message: Message, *, user, reason: str = "") -> Message:
    """Rejected; a rejected follow-up re-arms its sequence."""
    with transaction.atomic():
        message_service.transition(message, MessageStatus.REJECTED, user=user, reason=reason)
        sequence_service.on_follow_up_finished(message, MessageStatus.REJECTED)
    return message


def skip_company(message: Message, *, user, reason: str = "") -> Message:
    """Reject and tell the owner of `subject_ref` (this module knows no companies). Signals once, on commit."""
    subject_ref = message.thread.subject_ref
    with transaction.atomic():
        skip(message, user=user, reason=reason)
        transaction.on_commit(lambda: company_skipped.send(sender=Message, subject_ref=subject_ref, message=message))
    return message


def edit(message: Message, *, subject: str, body_text: str, user=None) -> Message:
    """New version written by `user` — no toolbox call."""
    message_service.ensure_transition(message, MessageStatus.SUPERSEDED)
    changes = {"subject": subject, "body_text": body_text, "edited_by_human": True, "created_by": user}
    return message_service.create_version(message, **changes)


def rewrite(message: Message, *, notes: str, automated: bool = False) -> Message:
    """New AI version with `notes` appended to the user prompt. Automated rewrites stop at the configured limit.

    An automated rewrite is counted on the stored message before the toolbox call, so failures count too.
    A toolbox failure yields a `failed` version and leaves the reviewed message in the queue.
    """
    message_service.ensure_transition(message, MessageStatus.SUPERSEDED)
    if not (message.rendered_prompt and message.template_version_id):
        raise ReviewError("only AI drafts can be rewritten")
    limit = communicator_settings.COMMUNICATOR_AUTOMATED_REWRITE_LIMIT
    if automated and not message_service.claim_automated_rewrite(message, limit=limit):
        raise RewriteLimitReachedError("automated rewrite limit reached")
    prompt = f"{message.rendered_prompt}\n\n{notes.strip()}"
    counters = {"review_notes": notes}
    try:
        draft = drafting_service.generate(
            prompt=prompt, version=message.template_version, tag=REWRITE_TAG, channel_idx=message.thread.channel.idx
        )
    except (ToolboxError, DraftOutputError) as error:
        return _failed_rewrite(message, error, counters)
    fields = {"subject": draft.subject, "body_text": draft.body_text, "model": draft.model, "usage": draft.usage}
    return message_service.create_version(
        message, rendered_prompt=prompt, attempts=draft.attempts, **fields, **counters
    )


def _failed_rewrite(message: Message, error: Exception, counters: dict) -> Message:
    code = drafting_service.failure_code(error)
    detail = drafting_service.failure_detail(error)
    failed = message_service.create_version(
        message, status=MessageStatus.FAILED, failure_code=code, failure_detail=detail, attempts=1, **counters
    )
    template_key = message.template_version.template.key
    ref = message.thread.subject_ref
    drafting_service.notify_failure(
        channel_idx=message.thread.channel.idx, subject_ref=ref, code=code, template_key=template_key
    )
    return failed
