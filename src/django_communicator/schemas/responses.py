# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Response schemas of the communicator admin API v2."""

from datetime import date, datetime, time
from typing import Any

from django_utils.toolbox import ModelInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    footer_html: str = Field(
        description="HTML footer as sent, legal text included; empty until sent or when the channel has none.",
        examples=[""],
    )
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
    audience: str = Field(description="Audience code; blank = every audience.", examples=[""])
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
    id: int = Field(description="Suppression id.", examples=[1])
    kind: str = Field(description="email, domain or email_token (always global).", examples=["domain"])
    value: str = Field(description="Lower-cased email or registrable domain.", examples=["shop.test"])
    scope: str = Field(description="channel: this channel only · install: every channel.", examples=["channel"])
    source: str = Field(
        description="manual, optout_confirmed, unsubscribe_link, bounce, complaint or erased.", examples=["manual"]
    )
    reason: str = Field(description="Free note.", examples=[""])
    created_at: datetime = Field(description="Created.", examples=["2026-09-13T12:00:00Z"])

    @classmethod
    def of(cls, row) -> "SuppressionResponse":
        fields = {name: getattr(row, name) for name in cls.model_fields if name not in ("id", "scope")}
        return cls(id=row.pk, scope="install" if row.channel_id is None else "channel", **fields)


class SuppressionListResponse(BaseModel):
    results: list[SuppressionResponse] = Field(description="Suppressions of the channel.", examples=[[]])


class AddressStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str = Field(description="unknown, valid, risky or invalid; only invalid blocks.", examples=["invalid"])
    reason: str = Field(description="Code: syntax, disposable, domain_not_found, null_mx, no_mail_server, mx_ok, "
                        "no_mx_address_fallback, hard_bounce or dns_error.", examples=["hard_bounce"])  # fmt: skip
    verified_at: datetime | None = Field(description="When it was written.", examples=["2026-09-13T12:00:00Z"])


class MatchingSuppressionResponse(BaseModel):
    id: int = Field(description="Suppression id.", examples=[1])
    kind: str = Field(description="email, domain or email_token.", examples=["email"])
    scope: str = Field(description="channel or install.", examples=["install"])
    source: str = Field(description="What wrote the row.", examples=["unsubscribe_link"])
    reason: str = Field(description="Free note.", examples=[""])
    created_at: datetime = Field(description="Created.", examples=["2026-09-13T12:00:00Z"])


class AddressExplainResponse(BaseModel):
    email: str = Field(description="The normalised address.", examples=["jan@shop.test"])
    refusal: str | None = Field(
        description="address_invalid, suppressed, or null: the channel may mail the address.", examples=["suppressed"]
    )
    suppressions: list[MatchingSuppressionResponse] = Field(description="Rows that match in this channel.")
    address_status: AddressStatusResponse | None = Field(description="Null: nothing known about the address.")


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
    autostart: bool = Field(description="Starts by itself after the first delivery of a thread.", examples=[False])
    steps: list[SequenceStepResponse] = Field(description="Steps by number.", examples=[[]])

    @classmethod
    def of(cls, sequence) -> "SequenceResponse":
        steps = [SequenceStepResponse.model_validate(step) for step in sequence.steps.all()]
        return cls(
            id=sequence.pk, key=sequence.key, is_active=sequence.is_active, autostart=sequence.autostart, steps=steps
        )


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


class ThreadDraftResponse(BaseModel):
    id: int = Field(description="Message id of the draft waiting for review.", examples=[11])
    subject: str = Field(description="Draft subject.", examples=["Quick question"])
    composed: bool = Field(description="Written by hand (`compose`), not drafted from a template.", examples=[False])


class ThreadWaitingResponse(BaseModel):
    id: int = Field(description="Message id of the mail waiting for the send beat.", examples=[12])
    status: str = Field(description="approved or scheduled.", examples=["scheduled"])
    scheduled_at: datetime | None = Field(description="Not before this moment.", examples=[None])
    next_slot: datetime | None = Field(description="Earliest send slot the policy allows.", examples=[None])


class ThreadRowResponse(ThreadSummaryResponse):
    activity_at: datetime = Field(description="Latest mail, draft or reply (`sort=activity`), else last mail.")
    subject: str = Field(description="Subject of the first outbound mail, else of the first reply.", examples=["Hi"])
    last_text: str = Field(description="Body of the latest mail, draft or reply (first 300 characters).", examples=[""])
    draft: ThreadDraftResponse | None = Field(description="The newest draft waiting for review.", examples=[None])
    waiting: ThreadWaitingResponse | None = Field(description="The first mail waiting to be sent.", examples=[None])
    reply_intent: str = Field(description="Class of the newest human reply; empty when none.", examples=["not_now"])
    needs_review: bool = Field(description="The newest human reply is flagged for a person.", examples=[False])

    @classmethod
    def of(cls, row: dict) -> "ThreadRowResponse":
        summary = ThreadSummaryResponse.model_validate(row["thread"]).model_dump()
        return cls(**summary, **{key: value for key, value in row.items() if key != "thread"})


class ThreadCountsResponse(BaseModel):
    all: int = Field(description="Threads of the channel (of the subject reference, when given).", examples=[14])
    draft: int = Field(description="Threads with a draft waiting for review.", examples=[2])
    waiting: int = Field(description="Threads with a mail waiting to be sent.", examples=[1])
    replied: int = Field(description="Threads in status replied.", examples=[9])


class ThreadListResponse(BaseModel):
    count: int = Field(description="Total matching threads.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[ThreadRowResponse] = Field(description="Newest first (`sort`).", examples=[[]])
    counts: ThreadCountsResponse = Field(description="Threads per state, ignoring the `state` filter.")


class ConversationDraftResponse(ThreadDraftResponse):
    recipient_email: str = Field(description="Recipient of the draft's own thread.", examples=["jan@shop.test"])


class ConversationRowResponse(ThreadRowResponse):
    """A conversation (one `subject_ref`): its newest thread, the state taken from every thread."""

    draft: ConversationDraftResponse | None = Field(
        description="The newest draft waiting for review in any thread of the conversation.", examples=[None]
    )
    waiting: ThreadWaitingResponse | None = Field(
        description="The first mail waiting to be sent in any thread of the conversation.", examples=[None]
    )
    thread_count: int = Field(description="Threads of the conversation.", examples=[2])
    replied: bool = Field(description="A thread of the conversation is in status replied.", examples=[True])


class ConversationCountsResponse(BaseModel):
    all: int = Field(description="Conversations of the channel.", examples=[12])
    draft: int = Field(description="Conversations with a draft waiting for review in any thread.", examples=[2])
    waiting: int = Field(description="Conversations with a mail waiting to be sent in any thread.", examples=[1])
    replied: int = Field(description="Conversations with a thread in status replied.", examples=[8])


class ConversationListResponse(BaseModel):
    count: int = Field(description="Total matching conversations.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[ConversationRowResponse] = Field(description="Latest conversation activity first.", examples=[[]])
    counts: ConversationCountsResponse = Field(
        description="Conversations per state, ignoring the `state` filter and following `needs_review`."
    )


class TimelineEntryResponse(BaseModel):
    kind: str = Field(description="message or reply.", examples=["reply"])
    message_id: int | None = Field(description="Message id; null for a reply.", examples=[None])
    reply_id: int | None = Field(description="Reply id of an inbound entry; null for a message.", examples=[5])
    composed: bool = Field(description="A message written by hand (`compose`); false for a reply.", examples=[False])
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
    intent: str = Field(description="Class of a reply; empty when none or for a message.", examples=["not_now"])
    intent_confidence: float | None = Field(description="Confidence of the class.", examples=[0.85])
    intent_return_date: date | None = Field(description="Return date of an out-of-office reply.", examples=[None])
    needs_review: bool = Field(description="A reply flagged for a person.", examples=[False])
    rejected_by: str = Field(
        description="Display name of who rejected (skipped) the draft; empty for a draft withdrawn because nobody may "
        "mail the recipient, and on every other entry.",
        examples=[""],
    )
    reject_reason: str = Field(description="Why the draft was rejected; empty when none.", examples=[""])

    @field_validator("rejected_by", mode="before")
    @classmethod
    def _reviewer_name(cls, value: Any) -> str:
        return value if isinstance(value, str) else _person_name(value)


class ThreadDetailResponse(ThreadSummaryResponse):
    subject_label: str = Field(
        description="What the thread is about, in the caller's words; empty when unknown.", examples=["Shop Ltd"]
    )
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
    intent: str = Field(description="Class of the reply; empty = not classified.", examples=["interested"])
    intent_confidence: float | None = Field(description="Confidence of the class.", examples=[0.9])
    intent_source: str = Field(description="toolbox; empty = not classified.", examples=["toolbox"])
    intent_return_date: date | None = Field(description="Return date of an out-of-office reply.", examples=[None])
    needs_review: bool = Field(description="The plan of the class asks a person to look.", examples=[False])
    plan_applied: dict[str, Any] = Field(
        description="What the plan did: sequence, notify, needs_review, resume_at.", examples=[{}]
    )


class ReplyClassProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    prompt_text: str = Field(description="System prompt naming the classes.", examples=["Classify the reply ..."])
    model: str = Field(description="Toolbox model id.", examples=["fake-chat"])
    min_confidence: float = Field(description="Below it the class is unclear.", examples=[0.7])
    max_chars: int = Field(description="Reply characters sent to the model.", examples=[3000])
    is_active: bool = Field(description="Classification on.", examples=[True])


class ReplyClassPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    intent: str = Field(description="Class.", examples=["out_of_office"])
    sequence: str = Field(description="stop, pause or pause_until_return.", examples=["pause_until_return"])
    notify: str = Field(description="Alert severity: low, medium or high.", examples=["low"])
    needs_review: bool = Field(description="Flag the reply for a person.", examples=[False])
    is_default: bool = Field(description="The code default (no stored plan).", examples=[True])


class ReplyClassPlanListResponse(BaseModel):
    results: list[ReplyClassPlanResponse] = Field(description="One plan per class.", examples=[[]])


class ReplyListResponse(BaseModel):
    count: int = Field(description="Total matching replies.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[ReplyResponse] = Field(description="Newest first.", examples=[[]])


class MailboxResponse(BaseModel):
    """Passwords are never returned: `smtp_password_set` / `imap_password_set` only."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Mailbox id.", examples=[2])
    address: str = Field(description="From address.", examples=["anna@mail.test"])
    display_name: str = Field(description="From name.", examples=["Anna Nowak"])
    cc_address: str = Field(description="Copy of every mail; empty: none.", examples=[""])
    user_id: int | None = Field(description="Staff member whose mailbox this is.", examples=[7])
    is_default: bool = Field(description="The channel default.", examples=[True])
    auth_method: str = Field(description="password (the only method).", examples=["password"])
    state: str = Field(description="active, paused, needs_login or disabled.", examples=["active"])
    smtp_host: str = Field(description="SMTP host.", examples=["smtp.mail.test"])
    smtp_port: int = Field(description="SMTP port.", examples=[587])
    smtp_security: str = Field(description="starttls or ssl.", examples=["starttls"])
    smtp_user: str = Field(description="SMTP login; empty: the address.", examples=[""])
    smtp_password_set: bool = Field(description="An SMTP password is stored.", examples=[True])
    smtp_login_failed_at: datetime | None = Field(description="SMTP refused the login.", examples=[None])
    send_enabled: bool = Field(description="Mail of new threads may leave from it.", examples=[True])
    imap_host: str = Field(description="IMAP host.", examples=["imap.mail.test"])
    imap_port: int = Field(description="IMAP port.", examples=[993])
    imap_use_ssl: bool = Field(description="IMAP over TLS.", examples=[True])
    imap_user: str = Field(description="IMAP login; empty: the address.", examples=[""])
    imap_password_set: bool = Field(description="An IMAP password is stored.", examples=[True])
    folder: str = Field(description="Folder polled.", examples=["INBOX"])
    last_uid: int = Field(description="Poll cursor.", examples=[0])
    receive_enabled: bool = Field(description="Polled by the beat.", examples=[True])
    last_polled_at: datetime | None = Field(description="Last successful poll.", examples=[None])
    daily_cap: int | None = Field(description="Mails a day; null: only the channel cap.", examples=[40])
    warmup_start_cap: int | None = Field(description="Warm-up: cap of day 0.", examples=[None])
    warmup_step: int = Field(description="Warm-up: added per day.", examples=[0])
    warmup_started_on: date | None = Field(description="Warm-up day 0.", examples=[None])
    paused_at: datetime | None = Field(description="Paused since.", examples=[None])
    pause_reason: str = Field(description="Why it is paused.", examples=[""])
    health_reset_at: datetime | None = Field(description="Health counts start here after a resume.", examples=[None])

    @classmethod
    def of(cls, mailbox, state: str) -> "MailboxResponse":
        derived = {"state", "smtp_password_set", "imap_password_set"}
        fields = {name: getattr(mailbox, name) for name in cls.model_fields if name not in derived}
        flags = {"smtp_password_set": bool(mailbox.smtp_password), "imap_password_set": bool(mailbox.imap_password)}
        return cls.model_validate({**fields, **flags, "state": state})


class MailboxListResponse(BaseModel):
    results: list[MailboxResponse] = Field(description="Mailboxes of the channel by address.", examples=[[]])


class MailboxTestResponse(BaseModel):
    smtp: str = Field(
        description="ok, not_configured, no_password, auth_failed, certificate (the host does not accept the server's "
        "certificate) or unreachable (the server does not answer). Logs in the way a send does.",
        examples=["ok"],
    )
    imap: str = Field(description="Same codes for the IMAP login.", examples=["auth_failed"])


class HealthMetricsResponse(BaseModel):
    sent: int = Field(description="Mails sent in the window.", examples=[120])
    hard_bounces: int = Field(description="Hard bounces.", examples=[2])
    soft_bounces: int = Field(description="Soft bounces waiting for a retry.", examples=[1])
    complaints: int = Field(description="Spam complaints.", examples=[0])
    optouts: int = Field(description="Confirmed opt-outs and unsubscribe links.", examples=[3])
    replies: int = Field(description="Human replies.", examples=[9])
    hard_bounces_pct: float | None = Field(description="Percent of sent; null while nothing was sent.", examples=[1.67])
    soft_bounces_pct: float | None = Field(description="Percent of sent.", examples=[0.83])
    complaints_pct: float | None = Field(description="Percent of sent.", examples=[0.0])
    optouts_pct: float | None = Field(description="Percent of sent.", examples=[2.5])
    replies_pct: float | None = Field(description="Percent of sent.", examples=[7.5])


class HealthWindowsResponse(BaseModel):
    last_24h: HealthMetricsResponse = Field(alias="24h", description="The last 24 hours.")
    last_7d: HealthMetricsResponse = Field(alias="7d", description="The last 7 days.")


class ChannelHealthResponse(BaseModel):
    windows: HealthWindowsResponse = Field(description="Every mailbox and the channel SMTP entry together.")


class MailboxHealthResponse(BaseModel):
    id: int = Field(description="Mailbox id.", examples=[2])
    address: str = Field(description="From address.", examples=["anna@mail.test"])
    state: str = Field(description="active, paused, needs_login or disabled.", examples=["active"])
    paused_at: datetime | None = Field(description="Paused since.", examples=[None])
    pause_reason: str = Field(description="Why it is paused.", examples=[""])
    sent_today: int = Field(description="Deliveries sent on the channel's current day.", examples=[12])
    cap_today: int | None = Field(description="Today's cap with the warm-up; null: only the channel cap.")
    windows: HealthWindowsResponse = Field(description="Counts and rates of this mailbox.")


class HealthReportResponse(BaseModel):
    channel: ChannelHealthResponse = Field(description="The whole channel.")
    mailboxes: list[MailboxHealthResponse] = Field(description="Each mailbox by address.", examples=[[]])

    @classmethod
    def of(cls, report: dict) -> "HealthReportResponse":
        rows = [
            {**row, "id": row["mailbox"].pk, "address": row["mailbox"].address, "paused_at": row["mailbox"].paused_at}
            | {"pause_reason": row["mailbox"].pause_reason}
            for row in report["mailboxes"]
        ]
        return cls.model_validate({"channel": report["channel"], "mailboxes": rows})


class HealthPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool = Field(description="Auto-pause on live channels.", examples=[False])
    window_hours: int = Field(description="Counting window.", examples=[168])
    min_sample: int = Field(description="Sent mails before a mailbox is judged.", examples=[50])
    max_bounce_pct: float = Field(description="Hard bounces %.", examples=[2.0])
    max_optout_pct: float = Field(description="Opt-outs %.", examples=[3.0])
    max_complaint_pct: float = Field(description="Spam complaints %.", examples=[0.1])


def _person_name(user) -> str:
    if user is None:
        return ""
    return user.get_full_name().strip() or user.get_username()


class PauseLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Log entry id.", examples=[1])
    mailbox_id: int = Field(description="Mailbox.", examples=[2])
    action: str = Field(description="pause or resume.", examples=["pause"])
    trigger: str = Field(description="bounce, optout, complaint or manual.", examples=["bounce"])
    reason: str = Field(description="Readable reason.", examples=["hard bounces 4.0% of 50 sent in 168 h, limit 2%"])
    metrics: dict[str, Any] = Field(description="Counts and rates measured at that moment.", examples=[{}])
    user_id: int | None = Field(description="Who; null for the automatic pause.", examples=[None])
    user_name: str = Field(
        description="Full name of the user, else the username; empty for the automatic pause.", examples=["Anna Nowak"]
    )
    created_at: datetime = Field(description="When.", examples=["2026-09-14T10:00:00Z"])

    @classmethod
    def of(cls, log) -> "PauseLogResponse":
        """`log.user` should be selected with the log: one query for a page."""
        fields = {field: getattr(log, field) for field in cls.model_fields if field != "user_name"}
        return cls(**fields, user_name=_person_name(log.user))


class PauseLogListResponse(BaseModel):
    count: int = Field(description="Total log entries.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[PauseLogResponse] = Field(description="Newest first.", examples=[[]])


class FooterResponse(BaseModel):
    language: str = Field(description="ISO 639-1 code.", examples=["pl"])
    html: str = Field(description="Sanitised footer HTML with `{{ legal }}`.", examples=["<div>{{ legal }}</div>"])
    modified_at: datetime = Field(description="Last saved.", examples=["2026-09-26T12:00:00Z"])

    @classmethod
    def of(cls, footer) -> "FooterResponse":
        return cls(language=footer.language.iso2.lower(), html=footer.html, modified_at=footer.modified_at)


class FooterListResponse(BaseModel):
    results: list[FooterResponse] = Field(description="Footers of the channel by language.", examples=[[]])


class RetryDraftsResponse(BaseModel):
    recovered: int = Field(description="Drafts moved from failed to review_required.", examples=[1])
    failed: int = Field(description="Drafts whose retry failed again.", examples=[0])


class ToolboxOutageResponse(BaseModel):
    down: bool = Field(description="Whether the simulated toolbox outage is on.", examples=[True])


class PollNowResponse(BaseModel):
    ingested: int = Field(description="Mail that produced or matched a reply.", examples=[1])
    skipped: int = Field(description="Mail dropped (unmatched, own, expunged).", examples=[0])
    quarantined: int = Field(description="Mail recorded in the quarantine (oversized, unreadable).", examples=[0])


class ApprovalRowResponse(BaseModel):
    id: int = Field(description="Message id.", examples=[12])
    channel: str = Field(description="Channel idx.", examples=["default-europe"])
    thread_id: int = Field(description="Thread id.", examples=[3])
    subject_ref: str = Field(description="Caller's reference of the thread.", examples=["leads.Company:42"])
    subject_label: str = Field(
        description="What the thread is about, in the caller's words (leads: the company name); empty when unknown.",
        examples=["Shop Ltd"],
    )
    recipient_email: str = Field(description="Recipient.", examples=["jan@shop.test"])
    recipient_name: str = Field(description="Recipient name.", examples=["Jan Kowalski"])
    template_key: str = Field(description="Template key; empty without a template version.", examples=["lead.cold"])
    subject: str = Field(description="Subject.", examples=["A question about your shop"])
    body_preview: str = Field(description="First characters of the text body.", examples=["Hello Jan,"])
    created_at: datetime = Field(description="Created.", examples=["2026-09-14T10:00:00Z"])
    age_hours: float = Field(description="Hours since it was created.", examples=[5.5])
    blocker: str | None = Field(description="Why approving would be refused; null when it may be approved.")


class ApprovalCountsResponse(BaseModel):
    total: int = Field(description="Drafts matching the filters.", examples=[3])
    by_channel: dict[str, int] = Field(description="Matching drafts per channel idx.", examples=[{"default-europe": 3}])
    by_mailbox: dict[str, int] = Field(
        description='Matching drafts per mailbox id; "none" = the channel SMTP entry.', examples=[{"2": 1, "none": 2}]
    )


class ApprovalListResponse(BaseModel):
    count: int = Field(description="Total matching drafts.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[ApprovalRowResponse] = Field(description="Oldest first.", examples=[[]])
    counts: ApprovalCountsResponse = Field(description="Counts of the whole filtered set.")


class BulkApprovalResponse(BaseModel):
    id: int = Field(description="Log entry id.", examples=[1])
    action: str = Field(description="approve or reject.", examples=["approve"])
    done: list[int] = Field(description="Ids approved or rejected.", examples=[[12]])
    failed: dict[str, str] = Field(description="Id → why it was not changed.", examples=[{"13": "message not found"}])


class BulkApprovalLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Log entry id.", examples=[1])
    user_id: int | None = Field(description="Who did it.", examples=[7])
    action: str = Field(description="approve or reject.", examples=["approve"])
    reason: str = Field(description="Rejection reason.", examples=[""])
    ids: list[int] = Field(description="Requested ids.", examples=[[12, 13]])
    done: list[int] = Field(description="Ids approved or rejected.", examples=[[12]])
    failed: dict[str, str] = Field(description="Id → why it was not changed.", examples=[{}])
    created_at: datetime = Field(description="When.", examples=["2026-09-14T10:00:00Z"])


class BulkApprovalLogListResponse(BaseModel):
    count: int = Field(description="Total log entries.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[BulkApprovalLogResponse] = Field(description="Newest first.", examples=[[]])
