# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Request schemas of the communicator admin API v2."""

from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from django_communicator.enums import ChannelMode, MessageStatus, ReplyKind, SuppressionKind, TemplateKind
from django_communicator.services.communicate_service import RecipientData


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


class DevStartSequenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: int = Field(description="Thread of the channel.", examples=[3])
    sequence_key: str = Field(min_length=1, max_length=64, description="Sequence key.", examples=["followup"])


class ThreadListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subject_ref: str | None = Field(default=None, max_length=200, description="Only threads about this reference.")


class ReplyListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: ReplyKind | None = Field(default=None, description="Only replies of this kind.")
    thread: int | None = Field(default=None, description="Only replies of this thread.")


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
