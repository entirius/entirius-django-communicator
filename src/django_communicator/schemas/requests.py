# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Request schemas of the communicator admin API v2."""

from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from django_communicator.enums import (
    ChannelMode,
    MessageStatus,
    NotifySeverity,
    ReplyIntent,
    ReplyKind,
    ReplySequenceAction,
    SuppressionKind,
    TemplateKind,
)
from django_communicator.services.communicate_service import RecipientData

AUDIENCE_PATTERN = r"^[A-Z0-9_]*$"
ROUTE_KEY_PATTERN = r"^[a-z0-9_-]*$"
MAILBOX_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ReviewListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: MessageStatus = Field(
        default=MessageStatus.REVIEW_REQUIRED, description="Only messages in this status.", examples=["review_required"]
    )


class RewriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str = Field(
        min_length=1, max_length=4000, description="Reviewer notes appended to the user prompt.", examples=["Shorter."]
    )


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=255, description="Edited subject.", examples=["Quick question"])
    body_text: str = Field(min_length=1, description="Edited plain-text body.", examples=["Hello, ..."])


class SkipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=255, description="Why the message is rejected.", examples=["Not a fit"])


class TemplateRequest(BaseModel):
    """Create or replace a template; a content change creates a new version."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128, description="Dotted template key.", examples=["lead.cold.shop"])
    kind: TemplateKind = Field(description="static or ai_prompt.", examples=["ai_prompt"])
    language: str = Field(min_length=2, max_length=2, description="ISO 639-1 language code.", examples=["pl"])
    audience: str = Field(
        default="",
        max_length=64,
        pattern=AUDIENCE_PATTERN,
        description="Caller's audience code (leads: lead type); blank = every audience. Omitted on PUT = unchanged.",
        examples=["RETAILER"],
    )
    subject: str = Field(default="", max_length=255, description="Subject (static) or a label.", examples=["Hello"])
    body: str = Field(
        min_length=1,
        description="Static body, or system prompt + '=== USER ===' line + user prompt.",
        examples=["Hi {first_name}"],
    )
    json_schema: dict[str, Any] | None = Field(default=None, description="Output schema (ai_prompt).", examples=[None])
    model: str = Field(default="", max_length=128, description="Toolbox model id (ai_prompt).", examples=["fake-chat"])
    requires_legal_footer: bool = Field(default=True, description="Refuse calls without a footer.", examples=[True])
    auto_approve: bool = Field(default=False, description="Static only: approve without review.", examples=[False])
    is_active: bool = Field(default=True, description="Inactive templates never resolve.", examples=[True])


class TestGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Placeholder values, recipient names included.",
        examples=[{"first_name": "Jan"}],
    )


class SuppressionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SuppressionKind = Field(description="email or domain.", examples=["domain"])
    value: str = Field(min_length=1, max_length=254, description="Email, or a domain / host.", examples=["shop.test"])
    reason: str = Field(default="", max_length=255, description="Why.", examples=["Asked not to be contacted"])


class DevCommunicateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_key: str = Field(min_length=1, max_length=128, description="Template key.", examples=["lead.cold.shop"])
    recipient: RecipientData = Field(description="Who the message is for.")
    context: dict[str, Any] = Field(default_factory=dict, description="Placeholder values.", examples=[{}])
    subject_ref: str = Field(min_length=1, max_length=200, description="Opaque reference.", examples=["bdd:c-01"])
    requires_review: bool = Field(default=True, description="False lets auto_approve templates pass.", examples=[True])
    audience: str = Field(
        default="", max_length=64, pattern=AUDIENCE_PATTERN, description="Audience code.", examples=["RETAILER"]
    )
    sender_route: str = Field(
        default="", max_length=64, pattern=ROUTE_KEY_PATTERN, description="Sender route key.", examples=["ecomhouse"]
    )


class WindowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(default=0, ge=0, description="Display order.", examples=[0])
    start_time: time = Field(description="Window start (inclusive), channel timezone.", examples=["08:00"])
    end_time: time = Field(description="Window end (exclusive), channel timezone.", examples=["17:00"])

    @model_validator(mode="after")
    def _end_after_start(self) -> "WindowRequest":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_days_only: bool = Field(default=True, description="Skip weekends and channel-country holidays.")
    daily_cap: int = Field(ge=0, le=10000, description="Deliveries per channel day.", examples=[10])
    spread: bool = Field(default=True, description="Spread the cap over the window runs.", examples=[True])
    windows: list[WindowRequest] = Field(description="Hour windows; replaced as a whole.", examples=[[]])


class OutboxQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: MessageStatus = Field(default=MessageStatus.APPROVED, description="Only messages in this status.")


class SequenceStepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1, le=100, description="Step number, from 1.", examples=[1])
    days_after_previous: int = Field(ge=0, le=365, description="Days after the previous delivery.", examples=[3])
    template_key: str = Field(min_length=1, max_length=128, description="Static template key.", examples=["followup"])


class SequenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        min_length=1, max_length=64, pattern=r"^[-a-zA-Z0-9_]+$", description="Slug.", examples=["followup"]
    )
    is_active: bool = Field(default=True, description="Schedules follow-ups.", examples=[True])
    autostart: bool = Field(
        default=False,
        description="Starts by itself after the first delivered message of a thread without a sequence. One per channel.",
        examples=[True],
    )
    steps: list[SequenceStepRequest] = Field(min_length=1, description="Steps with unique numbers.", examples=[[]])

    @model_validator(mode="after")
    def _unique_numbers(self) -> "SequenceRequest":
        if len({step.number for step in self.steps}) != len(self.steps):
            raise ValueError("step numbers must be unique")
        return self


class TextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4000, description="Follow-up text.", examples=["Just checking in."])
    is_active: bool = Field(default=True, description="Can be picked.", examples=[True])


class TextUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str | None = Field(
        default=None, min_length=1, max_length=4000, description="New follow-up text.", examples=["Any news?"]
    )
    is_active: bool | None = Field(default=None, description="Can be picked; true restores.", examples=[True])


class ChannelConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ChannelMode | None = Field(
        default=None, description="dry_run or sandbox; live is set in Django admin only.", examples=["sandbox"]
    )
    sandbox_mailbox: str | None = Field(
        default=None,
        max_length=254,
        pattern=r"^([^@\s]+@[^@\s]+\.[^@\s]+)?$",
        description="Sandbox recipient; empty clears.",
        examples=["sandbox@mail.test"],
    )
    default_sender_route: str | None = Field(
        default=None,
        max_length=64,
        pattern=ROUTE_KEY_PATTERN,
        description="Key of the sender route used when a thread names none; empty clears. 409 for an unknown or "
        "inactive route.",
        examples=["ecomhouse"],
    )


class DevClockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iso_datetime: datetime | None = Field(
        description="Channel clock; naive = channel timezone; null clears.", examples=["2026-09-14T10:00:00"]
    )


class DevToolboxOutageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    down: bool = Field(description="True simulates a toolbox outage, false ends it.", examples=[True])


class DevResetCountersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[date] = Field(
        min_length=1,
        max_length=31,
        description="Channel days whose send counters are cleared.",
        examples=[["2026-09-21"]],
    )


class SequenceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = Field(default=None, description="Schedules follow-ups.", examples=[True])
    autostart: bool | None = Field(
        default=None, description="Starts by itself after the first delivery. One per channel.", examples=[True]
    )


class DevStartSequenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: int = Field(description="Thread of the channel.", examples=[3])
    sequence_key: str = Field(min_length=1, max_length=64, description="Sequence key.", examples=["followup"])


class ThreadListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subject_ref: str | None = Field(default=None, max_length=200, description="Only threads about this reference.")
    state: Literal["draft", "waiting", "replied"] | None = Field(
        default=None,
        description="draft: a draft waits for review · waiting: a mail waits for the send beat · replied: a reply.",
    )
    sort: Literal["created", "activity"] = Field(
        default="created", description="created: newest thread first · activity: latest mail, draft or reply first."
    )


class ConversationListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    state: Literal["draft", "waiting", "replied"] | None = Field(
        default=None, description="A conversation is in a state when any of its threads is (see ThreadListQuery)."
    )


class ReplyListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: ReplyKind | None = Field(default=None, description="Only replies of this kind.")
    thread: int | None = Field(default=None, description="Only replies of this thread.")
    intent: ReplyIntent | None = Field(default=None, description="Only replies classified as this intent.")
    needs_review: bool | None = Field(default=None, description="Only replies flagged (or not) for a person to read.")


class MailboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    imap_host: str = Field(min_length=1, max_length=255, description="IMAP host.", examples=["imap.mail.test"])
    imap_port: int = Field(default=993, ge=1, le=65535, description="IMAP port.", examples=[993])
    imap_use_ssl: bool = Field(default=True, description="IMAP over TLS.", examples=[True])
    imap_user: str = Field(min_length=1, max_length=255, description="IMAP login.", examples=["outreach"])
    imap_password: str | None = Field(
        default=None, max_length=1024, description="Write-only; omitted or null keeps the stored one."
    )
    folder: str = Field(default="INBOX", min_length=1, max_length=64, description="Folder polled.", examples=["INBOX"])
    is_active: bool = Field(default=True, description="Polled by the beat.", examples=[True])


class FooterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    html: str = Field(
        min_length=1,
        max_length=20000,
        description="Footer HTML with `{{ legal }}` exactly once; sanitised to an allowlist on save.",
        examples=["<p><strong>Jan Kowalski</strong><br>Example sp. z o.o.</p><div>{{ legal }}</div>"],
    )


class ReplyActionRequest(BaseModel):
    """The whole next-step mapping of one intent class; omitted fields take the model defaults."""

    model_config = ConfigDict(extra="forbid")

    sequence: ReplySequenceAction = Field(
        default=ReplySequenceAction.STOP,
        description="stop (as every reply did before T4), pause, or pause_until_return (out_of_office only).",
        examples=["pause"],
    )
    notify: NotifySeverity = Field(
        default=NotifySeverity.HIGH,
        description="Alert severity for the sales admin; none = silent.",
        examples=["medium"],
    )
    remind_after_days: int | None = Field(
        default=None,
        ge=1,
        le=365,
        description="Alert a person to follow up this many days after the reply (out of office: only when the "
        "reply gave no return date).",
        examples=[30],
    )
    needs_review: bool = Field(default=False, description="Flag the reply for a person to read.", examples=[False])
    min_confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Below this confidence the class becomes unclear; null = COMMUNICATOR_REPLY_INTENT_MIN_CONFIDENCE.",
        examples=[0.8],
    )
    auto_suppress: bool = Field(
        default=False,
        description="unsubscribe only: confirm the opt-out (suppression) without a person, like confirm-optout.",
        examples=[False],
    )


class SenderRouteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        default="", max_length=128, description="Display name in the From header.", examples=["Wiktor W."]
    )
    from_email: str = Field(
        max_length=254,
        pattern=MAILBOX_PATTERN,
        description="Mailbox the mail leaves from.",
        examples=["wiktorw@ecom.house"],
    )
    cc_email: str = Field(
        default="",
        max_length=254,
        pattern=r"^([^@\s]+@[^@\s]+\.[^@\s]+)?$",
        description="Copy (CC) mailbox: the same person on the other domain; empty = no copy.",
        examples=["wiktorw@entirius.com"],
    )
    is_active: bool = Field(default=True, description="An inactive route cannot be used.", examples=[True])
    daily_cap: int | None = Field(
        default=None, ge=0, le=10000, description="Most deliveries per day from this From; null = none.", examples=[40]
    )
    warmup_start_cap: int | None = Field(
        default=None,
        ge=0,
        le=10000,
        description="Warm-up: the cap on the first day; null = no warm-up. Needs `daily_cap` as the ceiling.",
        examples=[5],
    )
    warmup_step: int = Field(default=1, ge=0, le=1000, description="Warm-up: cap added per day.", examples=[2])
    warmup_started_on: date | None = Field(
        default=None, description="Warm-up day one; empty with a warm-up = today (channel day).", examples=[None]
    )
    transport: Literal["smtp", "gmail_api"] = Field(
        default="smtp",
        description="smtp = the channel's SMTP connection; gmail_api = send through the mail account.",
        examples=["smtp"],
    )
    mail_account_id: int | None = Field(
        default=None,
        description="Mail account (`accounts/`) of this channel; required for gmail_api, its address must be "
        "`from_email`; null for smtp.",
        examples=[None],
    )


class SenderRouteCreateRequest(SenderRouteBody):
    key: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$", description="Route key.", examples=["ecomhouse"]
    )


class ApprovalQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    channel: str = Field(default="", max_length=128, description="Channel idx.", examples=["default-europe"])
    sender_route: str = Field(default="", max_length=64, description="Effective route key.", examples=["ecomhouse"])
    template: str = Field(default="", max_length=128, description="Template key.", examples=["lead.cold.shop"])
    q: str = Field(
        default="", max_length=200, description="Subject reference, recipient or subject.", examples=["shop"]
    )
    min_age_hours: float | None = Field(default=None, ge=0, le=100000, description="Waiting at least this long.")


class BulkApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"] = Field(description="approve or reject.", examples=["approve"])
    ids: list[int] = Field(min_length=1, max_length=1000, description="Message ids; the limit is a setting.")
    reason: str = Field(default="", max_length=255, description="Reject reason, stored on each message.", examples=[""])


class HealthPolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Auto-pause on.", examples=[True])
    window_hours: int = Field(default=168, ge=1, le=720, description="Judged window in hours.", examples=[168])
    min_sample: int = Field(default=50, ge=1, le=100000, description="Minimum sent mails in the window.", examples=[50])
    max_bounce_pct: float = Field(default=2, gt=0, le=100, description="Hard bounces, percent of sent.", examples=[2])
    max_optout_pct: float = Field(default=3, gt=0, le=100, description="Opt-outs, percent of sent.", examples=[3])
    max_complaint_pct: float = Field(default=0.1, gt=0, le=100, description="Complaints, percent.", examples=[0.1])


class RouteReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=255, description="Why; stored in the pause log.", examples=["checked"])


class MailAccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(
        max_length=254,
        pattern=MAILBOX_PATTERN,
        description="The Google mailbox to connect.",
        examples=["wiktorw@ecom.house"],
    )
    inbound_enabled: bool = Field(
        default=False, description="Also read the inbox for replies (asks Google for the read scope).", examples=[False]
    )


class MailAccountUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inbound_enabled: bool = Field(
        description="Read the inbox for replies. Turning it on needs a new sign-in to grant the read scope.",
        examples=[True],
    )


class MailAccountTestSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str = Field(
        max_length=254,
        pattern=MAILBOX_PATTERN,
        description="Recipient of the test message.",
        examples=["qa@example.test"],
    )
