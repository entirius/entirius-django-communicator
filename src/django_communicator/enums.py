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
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    SUPPRESSED = "suppressed", "Suppressed"
    WOULD_SEND = "would_send", "Would send"
    REJECTED = "rejected", "Rejected"
    SUPERSEDED = "superseded", "Superseded"
    SKIPPED = "skipped", "Skipped"


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
    SMTP = "smtp", "SMTP"
    BOUNCE = "bounce", "Bounce"
    SEND_OUTCOME_UNKNOWN = "send_outcome_unknown", "Send outcome unknown"


class ReplyKind(models.TextChoices):
    REPLY = "reply", "Reply"
    AUTO = "auto", "Autoresponder"
    SUSPECTED_OPTOUT = "suspected_optout", "Suspected opt-out"
    BOUNCE_HARD = "bounce_hard", "Hard bounce"
    BOUNCE_SOFT = "bounce_soft", "Soft bounce"


class QuarantineReason(models.TextChoices):
    OVERSIZED = "oversized", "Oversized"
    UNPARSEABLE = "unparseable", "Unparseable"
    DATA_ERROR = "data_error", "Data error"


class ReplyMatch(models.TextChoices):
    HEADER = "header", "Header"
    SENDER = "sender", "Sender"
    DSN = "dsn", "DSN"


class SequenceStopReason(models.TextChoices):
    REPLIED = "replied", "Replied"
    OPTOUT = "optout", "Opt-out"
    BOUNCE = "bounce", "Bounce"
    MANUAL = "manual", "Manual"
    FINISHED = "finished", "Finished"
    PAUSED = "paused", "Paused"
    FAILED = "failed", "Failed"
    SUPPRESSED = "suppressed", "Suppressed"


_S = MessageStatus
_DELIVERY_OUTCOMES = frozenset(
    {_S.SENT.value, _S.FAILED.value, _S.SUPPRESSED.value, _S.WOULD_SEND.value, _S.SKIPPED.value}
)

# The only legal status edges; `services.message_service.transition` enforces them. Terminal statuses map to
# an empty set. `scheduled` is an approved message waiting for an SMTP retry (4xx) or a soft-bounce retry;
# `sending` is the committed claim of one delivery (never re-sent: a stale claim ends `failed`); a delivery
# status notification moves a `sent` message to `failed` (hard) or back to `scheduled` (soft).
MESSAGE_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    _S.DRAFT.value: frozenset(
        {_S.REVIEW_REQUIRED.value, _S.APPROVED.value, _S.FAILED.value, _S.REJECTED.value, _S.SUPERSEDED.value}
    ),
    _S.REVIEW_REQUIRED.value: frozenset({_S.APPROVED.value, _S.REJECTED.value, _S.SUPERSEDED.value}),
    _S.APPROVED.value: frozenset({_S.SENDING.value, _S.SCHEDULED.value}),
    _S.SCHEDULED.value: frozenset({_S.SENDING.value}),
    _S.SENDING.value: _DELIVERY_OUTCOMES | {_S.SCHEDULED.value},
    _S.SENT.value: frozenset({_S.FAILED.value, _S.SCHEDULED.value}),
    _S.FAILED.value: frozenset(),
    _S.SUPPRESSED.value: frozenset(),
    _S.WOULD_SEND.value: frozenset(),
    _S.REJECTED.value: frozenset(),
    _S.SUPERSEDED.value: frozenset(),
    _S.SKIPPED.value: frozenset(),
}
