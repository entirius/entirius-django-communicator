# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Request schemas of the communicator admin API v2."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from django_communicator.enums import MessageStatus, SuppressionKind, TemplateKind
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
