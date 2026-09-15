# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Retry of AI drafts that failed transiently (toolbox down, timeout, 5xx) once the toolbox is reachable again.

Each retry is a new attempt of the whole draft generation from the stored prompt and template version: counted on
the row before the call, one `complete()`, recorded as a line of `failure_detail`. Success moves the draft to
`review_required` and nothing else. Budget, model and schema failures are never retried. The owner of the subject
(`draft_retry_requested`) can block a retry for good: the draft stays `failed` with `blocked_by_subject <reason>`.
"""

import logging

from django_utils.toolbox import ToolboxError, ToolboxStatus, status

from django_communicator import settings as communicator_settings
from django_communicator.enums import Direction, FailureCode, MessageStatus
from django_communicator.models import Message
from django_communicator.services import drafting_service, message_service, suppression_service
from django_communicator.services.communicate_service import DRAFT_TAG
from django_communicator.services.drafting_service import DraftOutputError
from django_communicator.signals import draft_retry_requested

logger = logging.getLogger(__name__)


def retry_failed_drafts() -> dict[str, int]:
    """Every retryable draft once; nothing while `status()` does not report the toolbox reachable."""
    counts = {"recovered": 0, "failed": 0}
    if status() != ToolboxStatus.CONFIGURED:
        return counts
    for message in candidates():
        outcome = retry(message)
        if outcome is not None:
            counts["recovered" if outcome else "failed"] += 1
    return counts


def candidates() -> list[Message]:
    """First-version AI drafts (no follow-ups, no rewrites) failed transiently, under the limit, not yet replaced."""
    failed = Message.objects.filter(
        status=MessageStatus.FAILED,
        failure_code=FailureCode.UPSTREAM,
        direction=Direction.OUT,
        parent=None,
        sequence_step=None,
        template_version__isnull=False,
        draft_retries__lt=communicator_settings.COMMUNICATOR_DRAFT_RETRY_LIMIT,
    ).exclude(rendered_prompt="")
    rows = failed.select_related("thread__channel", "template_version").order_by("pk")
    return [message for message in rows if drafting_service.is_transient(message.failure_detail) and _open(message)]


def _open(message: Message) -> bool:
    """Not replaced by a newer outbound message in the thread, recipient not suppressed since."""
    newer = Message.objects.filter(thread=message.thread_id, direction=Direction.OUT, pk__gt=message.pk)
    thread = message.thread
    return not newer.exists() and not suppression_service.is_suppressed(thread.channel, thread.recipient_email)


def retry(message: Message) -> bool | None:
    """True recovered, False failed again, None when the subject blocks it or another run claimed the retry first."""
    if _blocked_by_subject(message) or not message_service.claim_draft_retry(message):
        return None
    channel_idx = message.thread.channel.idx
    try:
        draft = drafting_service.generate(
            prompt=message.rendered_prompt, version=message.template_version, tag=DRAFT_TAG, channel_idx=channel_idx
        )
    except (ToolboxError, DraftOutputError) as error:
        _record_failure(message, error, channel_idx)
        return False
    history = f"{message.failure_detail}\nretry {message.draft_retries}: recovered"
    fields = {"subject": draft.subject, "body_text": draft.body_text, "model": draft.model, "usage": draft.usage}
    return message_service.recover_draft(message, attempts=draft.attempts, failure_detail=history, **fields) or None


def _blocked_by_subject(message: Message) -> bool:
    """A reason from a receiver ends the retries without spending one; a failing receiver skips this run only."""
    responses = draft_retry_requested.send_robust(sender=Message, message=message)
    errors = [response for _, response in responses if isinstance(response, Exception)]
    if errors:
        logger.error("communicator: subject check of draft %s failed: %r", message.pk, errors[0])
        return True
    reason = next((response for _, response in responses if response), None)
    if reason:
        history = f"{message.failure_detail}\nretry: blocked_by_subject {reason}"
        Message.objects.filter(pk=message.pk, status=MessageStatus.FAILED).update(failure_detail=history)
    return bool(reason)


def _record_failure(message: Message, error: Exception, channel_idx: str) -> None:
    """One history line; the alert fires only when this failure ends the retries (permanent or limit reached)."""
    detail = drafting_service.failure_detail(error)
    code = drafting_service.failure_code(error)
    history = f"{message.failure_detail}\nretry {message.draft_retries}: {detail}"
    Message.objects.filter(pk=message.pk, status=MessageStatus.FAILED).update(failure_code=code, failure_detail=history)
    exhausted = message.draft_retries >= communicator_settings.COMMUNICATOR_DRAFT_RETRY_LIMIT
    if exhausted or not drafting_service.is_transient(detail):
        template_key = message.template_version.template.key
        subject_ref = message.thread.subject_ref
        drafting_service.notify_failure(
            channel_idx=channel_idx, subject_ref=subject_ref, code=code, template_key=template_key
        )
