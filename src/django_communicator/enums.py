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
    EMAIL_TOKEN = "email_token", "Erased address token (all channels)"


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
    LEGAL_FOOTER = "legal_footer", "Legal footer missing"
    SENDER_ROUTE = "sender_route", "Sender route unusable"
    PRESEND_CHECK = "presend_check", "Address refused by the pre-send check"
    GMAIL_API = "gmail_api", "Gmail API"


class ReplyKind(models.TextChoices):
    REPLY = "reply", "Reply"
    AUTO = "auto", "Autoresponder"
    SUSPECTED_OPTOUT = "suspected_optout", "Suspected opt-out"
    BOUNCE_HARD = "bounce_hard", "Hard bounce"
    BOUNCE_SOFT = "bounce_soft", "Soft bounce"
    COMPLAINT = "complaint", "Spam complaint"


class ReplyIntent(models.TextChoices):
    """What a human reply wants (T4). `unclear` is also the class of a reply the classifier was not sure about."""

    INTERESTED = "interested", "Interested"
    NOT_NOW = "not_now", "Not now"
    NOT_INTERESTED = "not_interested", "Not interested"
    OUT_OF_OFFICE = "out_of_office", "Out of office"
    WRONG_PERSON = "wrong_person", "Wrong person"
    UNSUBSCRIBE = "unsubscribe", "Unsubscribe"
    UNCLEAR = "unclear", "Unclear"


class ReplySequenceAction(models.TextChoices):
    """What a classified reply does to the thread's sequence. `stop` is what every reply did before T4."""

    STOP = "stop", "Stop"
    PAUSE = "pause", "Pause"
    PAUSE_UNTIL_RETURN = "pause_until_return", "Pause until return"


class NotifySeverity(models.TextChoices):
    NONE = "none", "None"
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class QuarantineReason(models.TextChoices):
    OVERSIZED = "oversized", "Oversized"
    UNPARSEABLE = "unparseable", "Unparseable"
    DATA_ERROR = "data_error", "Data error"


class ReplyMatch(models.TextChoices):
    HEADER = "header", "Header"
    SENDER = "sender", "Sender"
    DSN = "dsn", "DSN"
    FEEDBACK = "feedback", "Feedback report"


class SequenceStopReason(models.TextChoices):
    REPLIED = "replied", "Replied"
    OPTOUT = "optout", "Opt-out"
    BOUNCE = "bounce", "Bounce"
    COMPLAINT = "complaint", "Spam complaint"
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


class HealthTrigger(models.TextChoices):
    """Why a sender route was paused (the pause log)."""

    BOUNCE = "bounce", "Hard bounce rate"
    OPTOUT = "optout", "Opt-out rate"
    COMPLAINT = "complaint", "Complaint rate"
    MANUAL = "manual", "Manual"


class RouteLogAction(models.TextChoices):
    PAUSE = "pause", "Pause"
    RESUME = "resume", "Resume"


class MailTransport(models.TextChoices):
    """How the mail of a sender route leaves. `smtp` is the channel's SMTP connection (the old behaviour)."""

    SMTP = "smtp", "SMTP"
    GMAIL_API = "gmail_api", "Gmail API"


class MailProvider(models.TextChoices):
    GMAIL = "gmail", "Gmail / Google Workspace"


class MailAccountState(models.TextChoices):
    NOT_CONNECTED = "not_connected", "Not connected"
    CONNECTED = "connected", "Connected"
    NEEDS_REAUTH = "needs_reauth", "Needs re-authorisation"
    ERROR = "error", "Error"
