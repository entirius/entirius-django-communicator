# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Response schemas of the communicator admin API v2."""

from datetime import date, datetime, time
from typing import Any

from django_utils.toolbox import ModelInfo
from pydantic import BaseModel, ConfigDict, Field

from django_communicator.enums import NotifySeverity, ReplyIntent, ReplySequenceAction


class ThreadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Thread id.", examples=[3])
    subject_ref: str = Field(description="Opaque reference of the caller.", examples=["leads.Company:42"])
    recipient_email: str = Field(description="Recipient email.", examples=["jan@shop.test"])
    recipient_name: str = Field(description="Recipient name.", examples=["Jan Kowalski"])
    recipient_language: str | None = Field(description="ISO 639-1 code.", examples=["pl"])
    status: str = Field(description="open, replied or closed.", examples=["open"])
    sender_route: str = Field(description="Sender route key set by the caller; empty = channel default.", examples=[""])

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
        description="no_template, render, budget, schema, model, upstream, smtp, bounce, send_outcome_unknown, "
        "legal_footer or sender_route.",
        examples=[""],
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


class MailboxCapResponse(BaseModel):
    route: str = Field(description="Sender route key.", examples=["ecomhouse"])
    from_email: str = Field(description="Mailbox.", examples=["wiktorw@ecom.house"])
    cap_today: int | None = Field(
        description="Deliveries allowed today (warm-up applied); null = no cap.", examples=[5]
    )
    sent_today: int = Field(description="Deliveries counted today from this mailbox.", examples=[0])
    warmup: bool = Field(description="A warm-up schedule is set.", examples=[True])


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
    mailboxes: list[MailboxCapResponse] = Field(
        description="Per-mailbox cap of each active route today.", examples=[[]]
    )


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
    autostart: bool = Field(description="Starts by itself after the first delivery.", examples=[True])
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
    default_sender_route: str = Field(
        description="Sender route key used when a thread has none.", examples=["ecomhouse"]
    )
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


class ThreadWaitingResponse(BaseModel):
    id: int = Field(description="Message id of the mail waiting for the send beat.", examples=[12])
    status: str = Field(description="approved or scheduled.", examples=["scheduled"])
    scheduled_at: datetime | None = Field(description="Not before this moment.", examples=[None])
    next_slot: datetime | None = Field(description="Earliest send slot the policy allows.", examples=[None])


class RowReplyIntentResponse(BaseModel):
    reply_id: int = Field(description="The classified reply the class comes from (the newest one).", examples=[5])
    intent: str = Field(
        description="interested, not_now, not_interested, out_of_office, wrong_person, unsubscribe or unclear.",
        examples=["interested"],
    )
    intent_confidence: float | None = Field(description="Classifier confidence 0..1.", examples=[0.92])
    intent_return_date: date | None = Field(description="Out of office: the day they are back.", examples=[None])
    needs_review: bool = Field(description="A person must read and decide.", examples=[False])


class ThreadRowResponse(ThreadSummaryResponse):
    activity_at: datetime = Field(description="Latest mail, draft or reply (`sort=activity`), else last mail.")
    subject: str = Field(description="Subject of the first outbound mail, else of the first reply.", examples=["Hi"])
    last_text: str = Field(description="Body of the latest mail, draft or reply (first 300 characters).", examples=[""])
    draft: ThreadDraftResponse | None = Field(description="The newest draft waiting for review.", examples=[None])
    waiting: ThreadWaitingResponse | None = Field(description="The first mail waiting to be sent.", examples=[None])
    reply_intent: RowReplyIntentResponse | None = Field(
        default=None,
        description="Class of the newest classified reply (any thread of a conversation); null when none.",
        examples=[None],
    )

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
    counts: ConversationCountsResponse = Field(description="Conversations per state, ignoring the `state` filter.")


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
    intent: str = Field(
        default="", description="Intent class of a reply; empty for a message or an unclassified reply.", examples=[""]
    )
    intent_confidence: float | None = Field(default=None, description="Classifier confidence 0..1.", examples=[None])
    intent_return_date: date | None = Field(
        default=None, description="Out of office: the day they are back.", examples=[None]
    )
    needs_review: bool = Field(default=False, description="A person must read and decide.", examples=[False])


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
    intent: str = Field(
        description="interested, not_now, not_interested, out_of_office, wrong_person, unsubscribe or unclear; "
        "empty when the reply was not classified (toolbox off or failed, auto reply, bounce).",
        examples=["interested"],
    )
    intent_confidence: float | None = Field(description="Classifier confidence 0..1.", examples=[0.92])
    intent_source: str = Field(description="`toolbox` when classified, else empty.", examples=["toolbox"])
    intent_return_date: date | None = Field(description="Out of office: the day they are back.", examples=[None])
    needs_review: bool = Field(
        description="A person must read and decide (class unclear, or the mapping says so).", examples=[False]
    )
    action_applied: dict = Field(
        description="The next-step plan applied to this reply (empty for bounces and auto replies).", examples=[{}]
    )
    resume_at: datetime | None = Field(description="Out of office: the paused sequence resumes then.", examples=[None])
    resumed_at: datetime | None = Field(description="When it resumed.", examples=[None])
    remind_at: datetime | None = Field(description="A reminder alert is due then.", examples=[None])
    reminded_at: datetime | None = Field(description="When the reminder was sent.", examples=[None])


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


class ReplyActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    intent: ReplyIntent = Field(description="The intent class.", examples=["not_now"])
    sequence: ReplySequenceAction = Field(description="What happens to the sequence.", examples=["pause"])
    notify: NotifySeverity = Field(description="Alert severity (none = silent).", examples=["medium"])
    remind_after_days: int | None = Field(description="Reminder after this many days.", examples=[30])
    needs_review: bool = Field(description="Flag for a person to read.", examples=[False])
    min_confidence: float | None = Field(description="Own threshold; null = module setting.", examples=[None])
    auto_suppress: bool = Field(description="Confirm an opt-out without a person (unsubscribe only).", examples=[False])
    is_default: bool = Field(description="No row: the code default applies.", examples=[True])


class ReplyActionListResponse(BaseModel):
    results: list[ReplyActionResponse] = Field(description="One entry per intent class.", examples=[[]])


class ReplyFollowupsResponse(BaseModel):
    resumed: int = Field(description="Sequences resumed after a return date.", examples=[1])
    reminded: int = Field(description="Reminders sent.", examples=[1])


class SenderRouteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str = Field(description="Route key.", examples=["ecomhouse"])
    name: str = Field(description="Display name in the From header.", examples=["Wiktor W."])
    from_email: str = Field(description="Mailbox the mail leaves from.", examples=["wiktorw@ecom.house"])
    cc_email: str = Field(description="Copy (CC) mailbox; empty = none.", examples=["wiktorw@entirius.com"])
    is_active: bool = Field(description="Usable.", examples=[True])
    is_default: bool = Field(description="The channel default route.", examples=[False])
    daily_cap: int | None = Field(description="Most deliveries per day from this From.", examples=[40])
    warmup_start_cap: int | None = Field(description="Warm-up first-day cap; null = no warm-up.", examples=[5])
    warmup_step: int = Field(description="Warm-up cap added per day.", examples=[2])
    warmup_started_on: date | None = Field(description="Warm-up day one.", examples=[None])
    cap_today: int | None = Field(description="Cap in force today; null = none.", examples=[5])
    sent_today: int = Field(description="Deliveries from this mailbox today.", examples=[0])
    paused: bool = Field(description="No mail leaves by this route until a manual resume (Q15).", examples=[False])
    paused_at: datetime | None = Field(description="When it was paused.", examples=[None])
    pause_reason: str = Field(description="Why: measured rates or the reason given by a person.", examples=[""])
    transport: str = Field(description="smtp or gmail_api.", examples=["smtp"])
    mail_account_id: int | None = Field(description="Mail account of a gmail_api route.", examples=[None])
    mail_account_state: str | None = Field(
        description="State of that account (a gmail_api route sends only while it is `connected`).", examples=[None]
    )


class SenderRouteListResponse(BaseModel):
    results: list[SenderRouteResponse] = Field(description="Routes of the channel.", examples=[[]])


class ApprovalRowResponse(BaseModel):
    id: int = Field(description="Message id.", examples=[11])
    channel: str = Field(description="Channel idx.", examples=["default-europe"])
    thread_id: int = Field(description="Thread id.", examples=[3])
    sender_route: str = Field(description="Effective route key; empty = none.", examples=["ecomhouse"])
    sender_from: str = Field(description="Mailbox the mail will leave from; empty = the channel's own.", examples=[""])
    sender_cc: str = Field(description="Copy mailbox.", examples=[""])
    company: str = Field(
        description="`company_name` of the caller's context, else the recipient name.", examples=["Shop"]
    )
    subject_ref: str = Field(description="Opaque reference of the caller.", examples=["leads.Company:42"])
    recipient_email: str = Field(description="Recipient.", examples=["jan@shop.test"])
    template_key: str = Field(description="Template key; empty without a template.", examples=["lead.cold.shop"])
    template_version: int | None = Field(description="Template version number.", examples=[2])
    subject: str = Field(description="Subject.", examples=["Quick question"])
    body_preview: str = Field(description="First 300 characters of the body.", examples=["Hello"])
    version: int = Field(description="Version in the rewrite chain.", examples=[1])
    created_at: datetime = Field(description="Created.", examples=["2026-09-13T12:00:00Z"])
    age_hours: float = Field(description="Hours waiting.", examples=[3.5])
    blocker: str = Field(
        description="Why approving would be refused (legal footer, route); empty = can approve.", examples=[""]
    )


class ApprovalCountsResponse(BaseModel):
    total: int = Field(description="Drafts matching the filters.", examples=[12])
    by_channel: dict[str, int] = Field(description="Per channel idx.", examples=[{}])
    by_route: dict[str, int] = Field(description="Per effective route key; empty key = none.", examples=[{}])


class ApprovalListResponse(BaseModel):
    count: int = Field(description="Total matching drafts.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    counts: ApprovalCountsResponse = Field(description="Totals for the filter chips (whole filtered set).")
    results: list[ApprovalRowResponse] = Field(description="Oldest first.", examples=[[]])


class BulkApprovalResponse(BaseModel):
    id: int = Field(description="Log entry id.", examples=[1])
    action: str = Field(description="approve or reject.", examples=["approve"])
    done: list[int] = Field(description="Message ids changed.", examples=[[1, 2]])
    failed: dict[str, str] = Field(description="Message id → why it was not changed.", examples=[{}])


class BulkApprovalLogResponse(BaseModel):
    id: int = Field(description="Log entry id.", examples=[1])
    user_id: int | None = Field(description="Who.", examples=[1])
    action: str = Field(description="approve or reject.", examples=["approve"])
    reason: str = Field(description="Reject reason.", examples=[""])
    requested: int = Field(description="Ids in the request.", examples=[3])
    done_ids: list[int] = Field(description="Message ids changed.", examples=[[1, 2]])
    failed: dict[str, str] = Field(description="Message id → why it was not changed.", examples=[{}])
    created_at: datetime = Field(description="When.", examples=["2026-09-13T12:00:00Z"])


class BulkApprovalLogListResponse(BaseModel):
    count: int = Field(description="Total entries.", examples=[1])
    next: str | None = Field(description="Next page URL.", examples=[None])
    previous: str | None = Field(description="Previous page URL.", examples=[None])
    results: list[BulkApprovalLogResponse] = Field(description="Newest first.", examples=[[]])


class HealthMetricsResponse(BaseModel):
    sent: int = Field(description="Mails sent in the window.", examples=[120])
    delivered: int = Field(description="Sent minus hard bounces.", examples=[117])
    hard_bounces: int = Field(description="Permanent bounces (DSN 5.x.x).", examples=[3])
    soft_bounces: int = Field(description="Temporary bounces (DSN 4.x.x) of mails that were retried.", examples=[1])
    optouts: int = Field(description="Confirmed opt-outs and unsubscribe-link clicks.", examples=[2])
    complaints: int = Field(description="Spam complaints (ARF); 0 while the feedback loop is off.", examples=[0])
    replies: int = Field(description="Human replies.", examples=[9])
    hard_bounce_rate_pct: float | None = Field(
        description="Percent of sent; null when nothing was sent.", examples=[2.5]
    )
    soft_bounce_rate_pct: float | None = Field(description="Percent of sent.", examples=[0.83])
    optout_rate_pct: float | None = Field(description="Percent of sent.", examples=[1.67])
    complaint_rate_pct: float | None = Field(description="Percent of sent.", examples=[0.0])
    reply_rate_pct: float | None = Field(description="Percent of sent.", examples=[7.5])


class HealthWindowsResponse(BaseModel):
    windows: dict[str, HealthMetricsResponse] = Field(description="Keys `24h` and `7d`.", examples=[{}])


class HealthRouteResponse(HealthWindowsResponse):
    route_key: str = Field(description="Route key; empty = mail without any route.", examples=["ecomhouse"])
    from_email: str = Field(description="From mailbox; empty for the no-route row.", examples=["wiktorw@ecom.house"])
    is_active: bool | None = Field(description="Null for the no-route row.", examples=[True])
    paused: bool = Field(description="Paused (automatically or by a person).", examples=[False])
    paused_at: datetime | None = Field(description="When it was paused.", examples=[None])
    pause_reason: str = Field(description="Why.", examples=[""])


class HealthPolicyResponse(BaseModel):
    enabled: bool = Field(description="Auto-pause on; off by default.", examples=[False])
    window_hours: int = Field(description="Window the thresholds are judged over.", examples=[168])
    min_sample: int = Field(
        description="Mails that must be sent in the window before any threshold counts.", examples=[50]
    )
    max_bounce_pct: float = Field(description="Hard bounces, percent of sent.", examples=[2.0])
    max_optout_pct: float = Field(description="Opt-outs, percent of sent.", examples=[3.0])
    max_complaint_pct: float = Field(description="Complaints, percent of sent.", examples=[0.1])


class HealthResponse(BaseModel):
    channel: HealthWindowsResponse = Field(description="All routes of the channel together.")
    routes: list[HealthRouteResponse] = Field(description="One row per route, ordered by key.", examples=[[]])
    policy: HealthPolicyResponse = Field(description="The auto-pause thresholds.")


class PauseLogRowResponse(BaseModel):
    id: int = Field(description="Log id.", examples=[1])
    route_key: str = Field(description="Route key.", examples=["ecomhouse"])
    action: str = Field(description="pause or resume.", examples=["pause"])
    trigger: str = Field(description="bounce, optout, complaint or manual.", examples=["bounce"])
    reason: str = Field(description="Measured rates or the person's reason.", examples=["hard bounces 3.0% ..."])
    metrics: dict[str, Any] = Field(description="Window counts at that moment.", examples=[{}])
    user: str | None = Field(description="Username of the person; null for the automatic pause.", examples=[None])
    created_at: datetime = Field(description="When.")


class PauseLogResponse(BaseModel):
    results: list[PauseLogRowResponse] = Field(description="Newest first, at most 100.", examples=[[]])


class MailAccountResponse(BaseModel):
    id: int = Field(description="Mail account id.", examples=[1])
    email: str = Field(description="The mailbox.", examples=["wiktorw@ecom.house"])
    provider: str = Field(description="gmail.", examples=["gmail"])
    state: str = Field(description="not_connected, connected, needs_reauth or error.", examples=["connected"])
    inbound_enabled: bool = Field(description="Replies are read from this inbox.", examples=[False])
    scopes: list[str] = Field(description="Scopes Google granted (no tokens are ever returned).", examples=[[]])
    can_send: bool = Field(description="Connected with the send scope.", examples=[True])
    can_receive: bool = Field(description="Connected with the read scope and `inbound_enabled`.", examples=[False])
    connected_at: datetime | None = Field(description="When the account was connected.", examples=[None])
    last_ok_at: datetime | None = Field(description="Last successful call to Google.", examples=[None])
    last_error: str = Field(description="Short code of the last problem; never a token.", examples=[""])
    last_error_at: datetime | None = Field(description="When it happened.", examples=[None])
    last_polled_at: datetime | None = Field(description="Last inbox read.", examples=[None])
    routes: list[str] = Field(
        description="Keys of the routes that send through this account.", examples=[["ecomhouse"]]
    )


class MailAccountListResponse(BaseModel):
    results: list[MailAccountResponse] = Field(description="Mail accounts of the channel.", examples=[[]])


class MailAccountConnectResponse(BaseModel):
    authorization_url: str = Field(
        description="Open this Google consent page in a browser.",
        examples=["https://accounts.google.com/o/oauth2/v2/auth?..."],
    )
    expires_in: int = Field(description="Seconds the sign-in link stays valid.", examples=[600])


class MailAccountTestResponse(BaseModel):
    ok: bool = Field(description="The connection works.", examples=[True])
    checks: list[str] = Field(description="What passed: token, profile.", examples=[["token"]])
    error: str = Field(description="Short code of the failure; empty when ok.", examples=[""])
    account: MailAccountResponse = Field(description="The account after the test (its state may have changed).")


class MailAccountTestSendResponse(BaseModel):
    ok: bool = Field(description="The test double accepted the message.", examples=[True])
    message_id: str = Field(description="Id the test double gave the message.", examples=["m1"])
