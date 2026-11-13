# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Request schemas of the communicator admin API v2."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from django_communicator.enums import (
    ChannelMode,
    MessageStatus,
    ReplyKind,
    SmtpSecurity,
    SuppressionScope,
    SuppressionSource,
    TemplateKind,
)
from django_communicator.services.communicate_service import RecipientData

AUDIENCE_PATTERN = r"^[A-Z0-9_]*$"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
OPTIONAL_EMAIL_PATTERN = r"^([^@\s]+@[^@\s]+\.[^@\s]+)?$"


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

    kind: Literal["email", "domain"] = Field(
        description="email or domain (`email_token` rows are written by erasure only).", examples=["domain"]
    )
    value: str = Field(min_length=1, max_length=254, description="Email, or a domain / host.", examples=["shop.test"])
    reason: str = Field(default="", max_length=255, description="Why.", examples=["Asked not to be contacted"])


class SuppressionListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str = Field(default="", max_length=254, description="Only rows with this value (lower-cased).")
    scope: SuppressionScope | None = Field(default=None, description="channel: rows of the channel · install: global.")
    source: SuppressionSource | None = Field(default=None, description="Only rows written by this source.")
    search: str = Field(default="", max_length=254, description="Part of the value, case-insensitive.")


class AddressStatusQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str = Field(min_length=3, max_length=254, description="The address to explain.", examples=["jan@shop.test"])


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
        description="Starts by itself after the first delivered templated message of a thread that never had a "
        "sequence. One per channel.",
        examples=[False],
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
        default=None, description="The channel's autostart sequence. One per channel.", examples=[True]
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


_MAILBOX_NULLABLE = frozenset({"user_id", "daily_cap", "warmup_start_cap", "warmup_started_on"})


class MailboxUpdateRequest(BaseModel):
    """Every field optional: only the fields sent change. A password is write-only; omitted or null keeps it."""

    model_config = ConfigDict(extra="forbid")

    address: str | None = Field(
        default=None, max_length=254, pattern=EMAIL_PATTERN, description="From address.", examples=["anna@mail.test"]
    )
    display_name: str | None = Field(default=None, max_length=128, description="From name.", examples=["Anna Nowak"])
    cc_address: str | None = Field(
        default=None, max_length=254, pattern=OPTIONAL_EMAIL_PATTERN, description="Copy of every mail; empty: none."
    )
    user_id: int | None = Field(default=None, description="Staff member whose mailbox this is; null: nobody.")
    is_default: bool | None = Field(default=None, description="Used when the caller names no user with a mailbox.")
    smtp_host: str | None = Field(default=None, max_length=255, description="SMTP host.", examples=["smtp.mail.test"])
    smtp_port: int | None = Field(default=None, ge=1, le=65535, description="SMTP port.", examples=[587])
    smtp_security: SmtpSecurity | None = Field(default=None, description="starttls or ssl.", examples=["starttls"])
    smtp_user: str | None = Field(default=None, max_length=255, description="SMTP login; empty: the address.")
    smtp_password: str | None = Field(default=None, min_length=1, max_length=1024, description="Write-only.")
    send_enabled: bool | None = Field(default=None, description="Mail of new threads may leave from it.")
    imap_host: str | None = Field(default=None, max_length=255, description="IMAP host.", examples=["imap.mail.test"])
    imap_port: int | None = Field(default=None, ge=1, le=65535, description="IMAP port.", examples=[993])
    imap_use_ssl: bool | None = Field(default=None, description="IMAP over TLS.", examples=[True])
    imap_user: str | None = Field(default=None, max_length=255, description="IMAP login; empty: the address.")
    imap_password: str | None = Field(default=None, min_length=1, max_length=1024, description="Write-only.")
    folder: str | None = Field(default=None, min_length=1, max_length=64, description="Folder polled.")
    receive_enabled: bool | None = Field(default=None, description="Polled by the beat.")
    daily_cap: int | None = Field(default=None, ge=0, le=100000, description="Mails a day; null: only the channel cap.")
    warmup_start_cap: int | None = Field(default=None, ge=0, le=100000, description="Warm-up: cap of day 0.")
    warmup_step: int | None = Field(default=None, ge=0, le=100000, description="Warm-up: added per day.")
    warmup_started_on: date | None = Field(default=None, description="Warm-up day 0; empty with a warm-up: today.")

    def changes(self) -> dict[str, Any]:
        """The fields the client sent. Null clears `user_id`, `daily_cap` and the warm-up; on any other field (a
        password included) it keeps the stored value."""
        sent = self.model_dump(exclude_unset=True)
        return {name: value for name, value in sent.items() if value is not None or name in _MAILBOX_NULLABLE}


class MailboxCreateRequest(MailboxUpdateRequest):
    address: str = Field(
        max_length=254, pattern=EMAIL_PATTERN, description="From address.", examples=["anna@mail.test"]
    )


class MailboxPauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=1, max_length=255, description="Why; logged.", examples=["Bounces after a list import"]
    )


class HealthPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Auto-pause on live channels.", examples=[True])
    window_hours: int = Field(default=168, ge=1, le=24 * 90, description="Counting window.", examples=[168])
    min_sample: int = Field(default=50, ge=1, le=100000, description="Sent mails before a mailbox is judged.")
    max_bounce_pct: Decimal = Field(default=Decimal(2), ge=0, le=100, decimal_places=2, description="Hard bounces %.")
    max_optout_pct: Decimal = Field(default=Decimal(3), ge=0, le=100, decimal_places=2, description="Opt-outs %.")
    max_complaint_pct: Decimal = Field(
        default=Decimal("0.1"), ge=0, le=100, decimal_places=2, description="Spam complaints %."
    )


class FooterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    html: str = Field(
        min_length=1,
        max_length=20000,
        description="Footer HTML with `{{ legal }}` exactly once; sanitised to an allowlist on save.",
        examples=["<p><strong>Jan Kowalski</strong><br>Example sp. z o.o.</p><div>{{ legal }}</div>"],
    )


class ApprovalQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    channel: str = Field(default="", max_length=128, description="Only drafts of this channel idx.")
    template_key: str = Field(default="", max_length=128, description="Only drafts of this template key.")
    search: str = Field(
        default="", max_length=200, description="Substring of the subject reference, recipient or subject."
    )
    min_age_hours: float | None = Field(default=None, ge=0, le=24 * 365, description="Drafts at least this old.")
    mailbox: int | None = Field(default=None, description="Only drafts of threads that send from this mailbox id.")


class BulkApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"] = Field(description="What to do with every id.", examples=["approve"])
    ids: list[int] = Field(
        min_length=1, description="Message ids; at most COMMUNICATOR_BULK_APPROVAL_MAX (100).", examples=[[12, 13]]
    )
    reason: str = Field(default="", max_length=255, description="Rejection reason, logged.", examples=[""])
