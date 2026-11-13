# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`communicate()` — the one call any module makes to get a message for a recipient by template key."""

from typing import Any

from django.utils import timezone
from django_utils.toolbox import ToolboxDisabledError, ToolboxError, is_enabled
from pydantic import BaseModel, Field

from django_communicator import settings as communicator_settings
from django_communicator.enums import FailureCode, MessageStatus, TemplateKind, ThreadStatus
from django_communicator.models import Channel, Message, MessageTemplate, Thread
from django_communicator.services import (
    address_service,
    channel_service,
    drafting_service,
    mailbox_service,
    message_service,
    suppression_service,
    template_service,
)
from django_communicator.services.drafting_service import DraftOutputError
from django_communicator.services.exceptions import ClientError
from django_communicator.services.render_service import RenderError, render

DRAFT_TAG = "communicator.draft"


class LegalFooterRequiredError(ClientError):
    """The template needs a legal footer and the caller passed none — no Message is created."""


class ThreadMismatchError(Exception):
    """The supplied thread belongs to another channel, subject or recipient — no Message is created."""


class RecipientData(BaseModel):
    email: str = Field(min_length=3, max_length=254, description="Recipient email.", examples=["jan@shop.test"])
    first_name: str = Field(default="", description="Recipient first name.", examples=["Jan"])
    last_name: str = Field(default="", description="Recipient last name.", examples=["Kowalski"])
    language: str = Field(default="", description="ISO 639-1 code of the recipient.", examples=["pl"])
    legal_footer: str = Field(default="", description="Footer carried verbatim on the message.", examples=[""])


def communicate(
    *,
    channel_idx: str,
    template_key: str,
    recipient: RecipientData,
    context: dict[str, Any],
    subject_ref: str,
    requires_review: bool = True,
    thread: Thread | None = None,
    audience: str = "",
    mailbox_user_id: int | None = None,
    subject_label: str = "",
) -> Message:
    """Address status and suppression (`can_mail`) → template → legal footer → render → static body or AI draft. Raises `Channel.DoesNotExist`.

    `audience` is the caller's opaque code (leads: the lead type); it picks the template variant, blank = default.
    `mailbox_user_id` picks the mailbox of a new thread: that user's sending mailbox in the channel, else the
    channel's default, else none (the channel SMTP entry); a thread keeps the mailbox of its first message.
    A non-empty `subject_label` (what the thread is about, e.g. a company name) is stored on the thread.
    Raises `ToolboxDisabledError` for an AI template while the toolbox is switched off — nothing is written."""
    channel = channel_service.get_channel(channel_idx)
    if thread is not None:
        check_thread(thread, channel, recipient, subject_ref)
    base = {"render_context": context, "legal_footer": recipient.legal_footer, "requires_review": requires_review}
    base.update(mailbox_user_id=mailbox_user_id, subject_label=subject_label)
    if refusal := mail_refusal(channel, recipient.email):
        fields = {**base, "failure_detail": refusal}
        return _create(channel, recipient, subject_ref, thread, status=MessageStatus.SUPPRESSED, **fields)
    try:
        template = template_service.resolve(channel, template_key, recipient.language, audience)
    except template_service.NoTemplateError:
        detail = f"no template {template_key}"
        return _fail(channel, recipient, subject_ref, thread, FailureCode.NO_TEMPLATE, detail, base)
    if template.requires_legal_footer and not recipient.legal_footer.strip():
        raise LegalFooterRequiredError(f"template {template_key} requires a legal footer")
    if template.kind != TemplateKind.STATIC and not is_enabled():
        raise ToolboxDisabledError()
    base["template_version"] = template.current_version
    values = {**context, "first_name": recipient.first_name, "last_name": recipient.last_name, "email": recipient.email}
    try:
        if template.kind == TemplateKind.STATIC:
            return _static(channel, recipient, subject_ref, thread, template, values, base)
        prompt = render(template.current_version.body, values)
    except RenderError as error:
        return _fail(channel, recipient, subject_ref, thread, FailureCode.RENDER, str(error), base)
    return _ai_draft(channel, recipient, subject_ref, thread, template, prompt, base)


def mail_refusal(channel: Channel, email: str) -> str | None:
    """`can_mail`; with `COMMUNICATOR_VERIFY_ENABLED` an unknown or stale address is checked first (DNS, no lock)."""
    if communicator_settings.COMMUNICATOR_VERIFY_ENABLED:
        address_service.verify_if_due(email, timezone.now())
    return suppression_service.can_mail(channel, email)


def _static(channel, recipient, subject_ref, thread, template: MessageTemplate, values: dict, base: dict) -> Message:
    version = template.current_version
    subject, body_text = render(version.subject, values), render(version.body, values)
    approved = template.auto_approve and not base["requires_review"]
    status = MessageStatus.APPROVED if approved else MessageStatus.REVIEW_REQUIRED
    return _create(channel, recipient, subject_ref, thread, status=status, subject=subject, body_text=body_text, **base)


def _ai_draft(channel, recipient, subject_ref, thread, template: MessageTemplate, prompt: str, base: dict) -> Message:
    try:
        draft = drafting_service.generate(
            prompt=prompt, version=template.current_version, tag=DRAFT_TAG, channel_idx=channel.idx
        )
    except (ToolboxError, DraftOutputError) as error:
        code = drafting_service.failure_code(error)
        drafting_service.notify_failure(
            channel_idx=channel.idx, subject_ref=subject_ref, code=code, template_key=template.key
        )
        detail = drafting_service.failure_detail(error)
        # A transient failure keeps the prompt for `draft_retry_service`; any other failure stores none.
        retry = {"rendered_prompt": prompt} if drafting_service.is_transient(detail) else {}
        return _fail(channel, recipient, subject_ref, thread, code, detail, {**base, "attempts": 1, **retry})
    return _create(
        channel,
        recipient,
        subject_ref,
        thread,
        status=MessageStatus.REVIEW_REQUIRED,
        subject=draft.subject,
        body_text=draft.body_text,
        rendered_prompt=prompt,
        model=draft.model,
        usage=draft.usage,
        attempts=draft.attempts,
        **base,
    )


def _fail(channel, recipient, subject_ref, thread, code: str, detail: str, base: dict) -> Message:
    fields = {**base, "failure_code": code, "failure_detail": detail}
    return _create(channel, recipient, subject_ref, thread, status=MessageStatus.FAILED, **fields)


def _create(channel, recipient: RecipientData, subject_ref: str, thread: Thread | None, **fields) -> Message:
    thread = thread or thread_for(channel, recipient, subject_ref)
    mailbox_service.assign(thread, fields.pop("mailbox_user_id"))
    label_thread(thread, fields.pop("subject_label"))
    return message_service.create_message(thread=thread, **fields)


def label_thread(thread: Thread, subject_label: str) -> None:
    """A non-empty label replaces the thread's; a blank one keeps it."""
    label = subject_label.strip()[: Thread._meta.get_field("subject_label").max_length]
    if not label or label == thread.subject_label:
        return
    thread.subject_label = label
    Thread.objects.filter(pk=thread.pk).update(subject_label=label, modified_at=timezone.now())


def check_thread(thread: Thread, channel: Channel, recipient: RecipientData, subject_ref: str) -> None:
    owner = (channel.pk, subject_ref, recipient.email.strip().lower())
    if (thread.channel_id, thread.subject_ref, thread.recipient_email) != owner:
        raise ThreadMismatchError(f"thread {thread.pk} belongs to another channel, subject or recipient")


def thread_for(
    channel: Channel, recipient: RecipientData, subject_ref: str, statuses: tuple[str, ...] = (ThreadStatus.OPEN,)
) -> Thread:
    """Newest thread in one of `statuses` for (channel, subject_ref, email), else a new one."""
    email = recipient.email.strip().lower()
    lookup = {"channel": channel, "subject_ref": subject_ref, "recipient_email": email}
    existing = Thread.objects.filter(status__in=statuses, **lookup).order_by("-created_at").first()
    if existing:
        return existing
    name = f"{recipient.first_name} {recipient.last_name}".strip()
    language = template_service.find_language(recipient.language)
    return Thread.objects.create(recipient_name=name, recipient_language=language, **lookup)
