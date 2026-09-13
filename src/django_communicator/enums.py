# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models


class ChannelMode(models.TextChoices):
    DRY_RUN = "dry_run", "Dry run"
    SANDBOX = "sandbox", "Sandbox"
    LIVE = "live", "Live"


class TemplateKind(models.TextChoices):
    STATIC = "static", "Static"
    AI_PROMPT = "ai_prompt", "AI prompt"


class ThreadStatus(models.TextChoices):
    OPEN = "open", "Open"
    REPLIED = "replied", "Replied"
    CLOSED = "closed", "Closed"


class Direction(models.TextChoices):
    OUT = "out", "Outbound"
    IN = "in", "Inbound"


class MessageStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    REVIEW_REQUIRED = "review_required", "Review required"
    APPROVED = "approved", "Approved"
    SCHEDULED = "scheduled", "Scheduled"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    SUPPRESSED = "suppressed", "Suppressed"
    WOULD_SEND = "would_send", "Would send"
    REJECTED = "rejected", "Rejected"
    SUPERSEDED = "superseded", "Superseded"


class SuppressionKind(models.TextChoices):
    EMAIL = "email", "Email"
    DOMAIN = "domain", "Domain"


class FailureCode(models.TextChoices):
    NO_TEMPLATE = "no_template", "No template"
    RENDER = "render", "Render"
    BUDGET = "budget", "Budget"
    SCHEMA = "schema", "Schema"
    MODEL = "model", "Model"
    UPSTREAM = "upstream", "Upstream"


_S = MessageStatus
_DELIVERY_OUTCOMES = frozenset({_S.SENT.value, _S.FAILED.value, _S.SUPPRESSED.value, _S.WOULD_SEND.value})

# The only legal status edges; `services.message_service.transition` enforces them. Terminal statuses map to
# an empty set. Sending edges (approved/scheduled → sent | failed | suppressed | would_send) are used from plan 06.
MESSAGE_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    _S.DRAFT.value: frozenset(
        {_S.REVIEW_REQUIRED.value, _S.APPROVED.value, _S.FAILED.value, _S.REJECTED.value, _S.SUPERSEDED.value}
    ),
    _S.REVIEW_REQUIRED.value: frozenset({_S.APPROVED.value, _S.REJECTED.value, _S.SUPERSEDED.value}),
    _S.APPROVED.value: _DELIVERY_OUTCOMES | {_S.SCHEDULED.value},
    _S.SCHEDULED.value: _DELIVERY_OUTCOMES,
    _S.SENT.value: frozenset(),
    _S.FAILED.value: frozenset(),
    _S.SUPPRESSED.value: frozenset(),
    _S.WOULD_SEND.value: frozenset(),
    _S.REJECTED.value: frozenset(),
    _S.SUPERSEDED.value: frozenset(),
}
