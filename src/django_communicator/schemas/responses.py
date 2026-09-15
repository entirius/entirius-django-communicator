# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Response schemas of the communicator admin API v2."""

from datetime import datetime, time
from typing import Any

from django_utils.toolbox import ModelInfo
from pydantic import BaseModel, ConfigDict, Field


class ThreadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Thread id.", examples=[3])
    subject_ref: str = Field(description="Opaque reference of the caller.", examples=["leads.Company:42"])
    recipient_email: str = Field(description="Recipient email.", examples=["jan@shop.test"])
    recipient_name: str = Field(description="Recipient name.", examples=["Jan Kowalski"])
    recipient_language: str | None = Field(description="ISO 639-1 code.", examples=["pl"])
    status: str = Field(description="open, replied or closed.", examples=["open"])

    @classmethod
    def of(cls, thread) -> "ThreadResponse":
        language = thread.recipient_language.iso2.lower() if thread.recipient_language else None
        return cls.model_validate({**cls._fields(thread), "recipient_language": language})

    @classmethod
    def _fields(cls, thread) -> dict:
        return {name: getattr(thread, name) for name in cls.model_fields if name != "recipient_language"}


class TemplateRefResponse(BaseModel):
    template_id: int = Field(description="Template id.", examples=[1])
    key: str = Field(description="Template key.", examples=["lead.cold.shop"])
    kind: str = Field(description="static or ai_prompt.", examples=["ai_prompt"])
    version_id: int = Field(description="Template version id the message was rendered with.", examples=[4])
    version_number: int = Field(description="Version number.", examples=[2])


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Message id.", examples=[11])
    status: str = Field(description="Message status.", examples=["review_required"])
    direction: str = Field(description="out or in.", examples=["out"])
    subject: str = Field(description="Subject.", examples=["Quick question"])
    body_text: str = Field(description="Plain-text body (no footer).", examples=["Hello, ..."])
    render_context: dict[str, Any] = Field(description="The caller's context, for the reviewer.", examples=[{}])
    rendered_prompt: str = Field(description="Prompt sent to the toolbox (AI drafts).", examples=[""])
    model: str = Field(description="Model that wrote the draft.", examples=["fake-chat"])
    usage: dict[str, Any] = Field(description="input_tokens, output_tokens, cost.", examples=[{}])
    attempts: int = Field(description="Toolbox calls made for this version.", examples=[1])
    version: int = Field(description="Version in the rewrite chain.", examples=[1])
    parent_id: int | None = Field(description="Previous version.", examples=[None])
    automated_rewrites: int = Field(description="Automated rewrites in the chain.", examples=[0])
    edited_by_human: bool = Field(description="Written by a reviewer.", examples=[False])
    requires_review: bool = Field(description="The caller asked for review.", examples=[True])
    reviewed_by_id: int | None = Field(description="Reviewer user id.", examples=[None])
    reviewed_at: datetime | None = Field(description="When reviewed.", examples=[None])
    reject_reason: str = Field(description="Why rejected.", examples=[""])
    review_notes: str = Field(description="Notes that produced this version.", examples=[""])
    legal_footer: str = Field(description="Footer carried verbatim.", examples=[""])
    scheduled_at: datetime | None = Field(description="Send slot; empty = due at once.", examples=[None])
    sent_at: datetime | None = Field(
        description="Delivered (sent or would_send) on the channel clock.", examples=[None]
    )
    message_id: str = Field(description="Our Message-ID header once sent.", examples=[""])
    send_attempts: int = Field(description="SMTP deliveries tried.", examples=[0])
    replied_at: datetime | None = Field(description="A header-matched reply answered it.", examples=[None])
    failure_code: str = Field(
        description="no_template, render, budget, schema, model, upstream or smtp.", examples=[""]
    )
    failure_detail: str = Field(description="Error class and codes — never the prompt.", examples=[""])
    created_at: datetime = Field(description="Created.", examples=["2026-09-13T12:00:00Z"])


class MessageDetailResponse(MessageResponse):
    thread: ThreadResponse = Field(description="Conversation the message belongs to.")
    template: TemplateRefResponse | None = Field(description="Template version used; null when none.")

    @classmethod
    def of(cls, message) -> "MessageDetailResponse":
        version = message.template_version
        template = None
        if version:
            ref = {"template_id": version.template_id, "key": version.template.key, "kind": version.template.kind}
            template = TemplateRefResponse(version_id=version.pk, version_number=version.number, **ref)
        base = MessageResponse.model_validate(message).model_dump()
        return cls(**base, thread=ThreadResponse.of(message.thread), template=template)


class MessageListResponse(BaseModel):
    count: int = Field(description="Total matching messages.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[MessageDetailResponse] = Field(description="Oldest first.", examples=[[]])


class TemplateVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Version id.", examples=[4])
    number: int = Field(description="Version number.", examples=[2])
    subject: str = Field(description="Subject.", examples=["Hello"])
    body: str = Field(description="Body or prompt.", examples=["Hi {first_name}"])
    json_schema: dict[str, Any] | None = Field(description="Output schema.", examples=[None])
    model: str = Field(description="Toolbox model id.", examples=["fake-chat"])
    created_by_id: int | None = Field(description="Author user id.", examples=[None])
    created_at: datetime = Field(description="Created.", examples=["2026-09-13T12:00:00Z"])


class TemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Template id.", examples=[1])
    key: str = Field(description="Template key.", examples=["lead.cold.shop"])
    kind: str = Field(description="static or ai_prompt.", examples=["ai_prompt"])
    language: str = Field(description="ISO 639-1 code.", examples=["pl"])
    subject: str = Field(description="Subject.", examples=["Hello"])
    body: str = Field(description="Body or prompt.", examples=["Hi {first_name}"])
    json_schema: dict[str, Any] | None = Field(description="Output schema.", examples=[None])
    model: str = Field(description="Toolbox model id.", examples=["fake-chat"])
    requires_legal_footer: bool = Field(description="Calls need a footer.", examples=[True])
    auto_approve: bool = Field(description="Static only.", examples=[False])
    is_active: bool = Field(description="Resolvable.", examples=[True])
    current_version_id: int | None = Field(description="Current version id.", examples=[4])
    current_version_number: int | None = Field(description="Current version number.", examples=[2])

    @classmethod
    def of(cls, template) -> "TemplateResponse":
        version = template.current_version
        fields = {name: getattr(template, name) for name in cls.model_fields if name not in _TEMPLATE_COMPUTED}
        return cls(
            **fields,
            language=template.language.iso2.lower(),
            current_version_id=version.pk if version else None,
            current_version_number=version.number if version else None,
        )


_TEMPLATE_COMPUTED = frozenset({"language", "current_version_id", "current_version_number"})


class TemplateListResponse(BaseModel):
    results: list[TemplateResponse] = Field(description="Templates of the channel by key.", examples=[[]])


class TemplateVersionListResponse(BaseModel):
    results: list[TemplateVersionResponse] = Field(description="Newest first.", examples=[[]])


class DraftPreviewResponse(BaseModel):
    subject: str = Field(description="Generated or rendered subject.", examples=["Quick question"])
    body_text: str = Field(description="Generated or rendered body.", examples=["Hello, ..."])
    rendered_prompt: str = Field(description="Prompt sent (ai_prompt), empty for static.", examples=[""])
    model: str = Field(description="Model used.", examples=["fake-chat"])
    usage: dict[str, Any] = Field(description="Usage of the call.", examples=[{}])


class ModelListResponse(BaseModel):
    results: list[ModelInfo] = Field(
        description="Toolbox catalogue entries allowed for the channel, field names unchanged.", examples=[[]]
    )


class SuppressionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Suppression id.", examples=[1])
    kind: str = Field(description="email, domain or email_token (global).", examples=["domain"])
    value: str = Field(description="Lower-cased email or registrable domain.", examples=["shop.test"])
    reason: str = Field(description="Why.", examples=[""])
    created_at: datetime = Field(description="Created.", examples=["2026-09-13T12:00:00Z"])


class SuppressionListResponse(BaseModel):
    results: list[SuppressionResponse] = Field(description="Suppressions of the channel.", examples=[[]])


class OutboxMessageResponse(MessageDetailResponse):
    next_slot: datetime | None = Field(description="Earliest send slot of a waiting message.", examples=[None])


class OutboxListResponse(BaseModel):
    count: int = Field(description="Total matching messages.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[OutboxMessageResponse] = Field(description="Oldest first.", examples=[[]])


class WindowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order: int = Field(description="Display order.", examples=[0])
    start_time: time = Field(description="Start (inclusive).", examples=["08:00:00"])
    end_time: time = Field(description="End (exclusive).", examples=["17:00:00"])


class PolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_days_only: bool = Field(description="Weekends and holidays skipped.", examples=[True])
    daily_cap: int = Field(description="Deliveries per channel day.", examples=[10])
    spread: bool = Field(description="Cap spread over the window runs.", examples=[True])
    windows: list[WindowResponse] = Field(description="Hour windows.", examples=[[]])
    timezone: str = Field(description="Channel timezone.", examples=["Europe/Warsaw"])
    country: str = Field(description="Holiday country.", examples=["PL"])
    sent_today: int = Field(description="Deliveries counted today on the channel clock.", examples=[0])
    next_slot: datetime | None = Field(description="Next open moment from the channel clock.", examples=[None])


class SequenceStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Step id.", examples=[1])
    number: int = Field(description="Step number.", examples=[1])
    days_after_previous: int = Field(description="Days after the previous delivery.", examples=[3])
    template_key: str = Field(description="Static template key.", examples=["followup"])


class SequenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Sequence id.", examples=[1])
    key: str = Field(description="Slug.", examples=["followup"])
    is_active: bool = Field(description="Schedules follow-ups.", examples=[True])
    steps: list[SequenceStepResponse] = Field(description="Steps by number.", examples=[[]])

    @classmethod
    def of(cls, sequence) -> "SequenceResponse":
        steps = [SequenceStepResponse.model_validate(step) for step in sequence.steps.all()]
        return cls(id=sequence.pk, key=sequence.key, is_active=sequence.is_active, steps=steps)


class SequenceListResponse(BaseModel):
    results: list[SequenceResponse] = Field(description="Sequences of the channel by key.", examples=[[]])


class SequenceStepListResponse(BaseModel):
    results: list[SequenceStepResponse] = Field(description="Steps by number.", examples=[[]])


class TextResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Text id.", examples=[1])
    body: str = Field(description="Follow-up text.", examples=["Just checking in."])
    is_active: bool = Field(description="Can be picked.", examples=[True])


class TextListResponse(BaseModel):
    results: list[TextResponse] = Field(description="Texts of the sequence.", examples=[[]])


class ChannelConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    idx: str = Field(description="Channel idx.", examples=["default-europe"])
    label: str = Field(description="Label.", examples=["Default Europe"])
    mode: str = Field(description="dry_run, sandbox or live.", examples=["sandbox"])
    sandbox_mailbox: str = Field(description="Sandbox recipient.", examples=["sandbox@mail.test"])
    live_enabled: bool = Field(description="Live mode allowed.", examples=[False])
    timezone: str = Field(description="Timezone.", examples=["Europe/Warsaw"])
    country: str = Field(description="Holiday country.", examples=["PL"])


class SendDueResponse(BaseModel):
    sent: int = Field(description="Delivered over SMTP.", examples=[1])
    would_send: int = Field(description="Recorded by dry_run channels.", examples=[0])
    suppressed: int = Field(description="Recipient suppressed after approval.", examples=[0])
    failed: int = Field(description="Failed on SMTP.", examples=[0])
    deferred: int = Field(description="Left for a later run (window, cap, spread, 4xx).", examples=[0])
    follow_ups_scheduled: int = Field(description="Follow-ups created before sending.", examples=[0])


class ClockResponse(BaseModel):
    now: datetime = Field(description="Channel clock after the change.", examples=["2026-09-14T10:00:00+02:00"])


class CountersResetResponse(BaseModel):
    cleared: int = Field(description="Channel days cleared.", examples=[7])


class SequenceStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    thread_id: int = Field(description="Thread id.", examples=[3])
    sequence_id: int = Field(description="Sequence id.", examples=[1])
    step: int = Field(description="Follow-ups scheduled so far.", examples=[0])
    next_due_at: datetime | None = Field(description="Next follow-up due.", examples=[None])
    stopped_at: datetime | None = Field(description="Stopped or paused; empty while running.", examples=[None])
    stop_reason: str = Field(
        description="replied, optout, bounce, paused, finished, …; empty while running.", examples=[""]
    )


class ThreadSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Thread id.", examples=[3])
    subject_ref: str = Field(description="Opaque reference of the caller.", examples=["leads.Company:42"])
    recipient_email: str = Field(description="Recipient email.", examples=["jan@shop.test"])
    recipient_name: str = Field(description="Recipient name.", examples=["Jan Kowalski"])
    status: str = Field(description="open, replied or closed.", examples=["replied"])
    last_message_at: datetime | None = Field(description="Last outbound or inbound mail.", examples=[None])


class ThreadListResponse(BaseModel):
    count: int = Field(description="Total matching threads.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[ThreadSummaryResponse] = Field(description="Newest first.", examples=[[]])


class TimelineEntryResponse(BaseModel):
    kind: str = Field(description="message or reply.", examples=["reply"])
    message_id: int | None = Field(description="Message id; null for a reply.", examples=[None])
    at: datetime = Field(description="Sent, received or created.", examples=["2026-09-14T10:15:00Z"])
    direction: str = Field(description="out or in.", examples=["in"])
    status: str = Field(description="Message status; empty for a reply.", examples=[""])
    subject: str = Field(description="Subject.", examples=["Re: Your shop audit"])
    body_text: str = Field(description="Plain-text body, quoted history included.", examples=["Hello, ..."])
    from_email: str = Field(description="Sender of a reply; empty for our messages.", examples=["owner@shop.test"])
    reply_kind: str = Field(
        description="reply, auto, suspected_optout, bounce_hard or bounce_soft; empty for a message.",
        examples=["reply"],
    )


class ThreadDetailResponse(ThreadSummaryResponse):
    sequence: SequenceStateResponse | None = Field(description="Follow-up sequence of the thread.", examples=[None])
    timeline: list[TimelineEntryResponse] = Field(description="Messages and replies, oldest first.", examples=[[]])


class ReplyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Reply id.", examples=[5])
    thread_id: int = Field(description="Thread.", examples=[3])
    message_id: int | None = Field(description="Outbound message answered (header match).", examples=[11])
    from_email: str = Field(description="Sender.", examples=["owner@shop.test"])
    subject: str = Field(description="Subject.", examples=["Re: Your shop audit"])
    body_text: str = Field(description="Plain-text body.", examples=["Hello, ..."])
    kind: str = Field(description="reply, auto, suspected_optout, bounce_hard or bounce_soft.", examples=["reply"])
    matched_by: str = Field(description="header, sender or dsn.", examples=["header"])
    inbound_message_id: str = Field(description="Message-ID of the inbound mail.", examples=["<a@shop.test>"])
    received_at: datetime = Field(description="Ingested.", examples=["2026-09-14T10:15:00Z"])
    optout_confirmed_at: datetime | None = Field(description="Opt-out confirmed by a human.", examples=[None])


class ReplyListResponse(BaseModel):
    count: int = Field(description="Total matching replies.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[ReplyResponse] = Field(description="Newest first.", examples=[[]])


class MailboxResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    imap_host: str = Field(description="IMAP host.", examples=["imap.mail.test"])
    imap_port: int = Field(description="IMAP port.", examples=[993])
    imap_use_ssl: bool = Field(description="IMAP over TLS.", examples=[True])
    imap_user: str = Field(description="IMAP login.", examples=["outreach"])
    has_password: bool = Field(description="A password is stored (never returned).", examples=[True])
    folder: str = Field(description="Folder polled.", examples=["INBOX"])
    last_uid: int = Field(description="Poll cursor.", examples=[0])
    is_active: bool = Field(description="Polled by the beat.", examples=[True])
    last_polled_at: datetime | None = Field(description="Last successful poll.", examples=[None])

    @classmethod
    def of(cls, config) -> "MailboxResponse":
        fields = {name: getattr(config, name) for name in cls.model_fields if name != "has_password"}
        return cls.model_validate({**fields, "has_password": bool(config.imap_password)})


class PollNowResponse(BaseModel):
    ingested: int = Field(description="Mail that produced or matched a reply.", examples=[1])
    skipped: int = Field(description="Mail dropped (unmatched, own, expunged).", examples=[0])
    quarantined: int = Field(description="Mail recorded in the quarantine (oversized, unreadable).", examples=[0])
