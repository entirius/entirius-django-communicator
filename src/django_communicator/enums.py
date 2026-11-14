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


class SuppressionScope(models.TextChoices):
    """`COMMUNICATOR_SUPPRESSION_SCOPE`, and the scope of one row: a channel row or a global row (no channel)."""

    CHANNEL = "channel", "Channel"
    INSTALL = "install", "Install"


class SuppressionSource(models.TextChoices):
    MANUAL = "manual", "Manual"
    OPTOUT_CONFIRMED = "optout_confirmed", "Opt-out confirmed"
    UNSUBSCRIBE_LINK = "unsubscribe_link", "Unsubscribe link"
    BOUNCE = "bounce", "Hard bounce"
    COMPLAINT = "complaint", "Spam complaint"
    ERASED = "erased", "Erased or anonymised"


class AddressState(models.TextChoices):
    """Status of one address; only `invalid` blocks mail."""

    UNKNOWN = "unknown", "Unknown"
    VALID = "valid", "Valid"
    RISKY = "risky", "Risky"
    INVALID = "invalid", "Invalid"


class AddressReason(models.TextChoices):
    SYNTAX = "syntax", "Syntax"
    DISPOSABLE = "disposable", "Disposable domain"
    DOMAIN_NOT_FOUND = "domain_not_found", "Domain not found"
    NULL_MX = "null_mx", "Null MX"
    NO_MAIL_SERVER = "no_mail_server", "No mail server"
    MX_OK = "mx_ok", "MX found"
    NO_MX_ADDRESS_FALLBACK = "no_mx_address_fallback", "No MX, address record only"
    HARD_BOUNCE = "hard_bounce", "Hard bounce"
    DNS_ERROR = "dns_error", "DNS error"


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
    MAILBOX = "mailbox", "Mailbox cannot send"
    RECIPIENT = "recipient", "Recipient refused"


class ReplyKind(models.TextChoices):
    REPLY = "reply", "Reply"
    AUTO = "auto", "Autoresponder"
    SUSPECTED_OPTOUT = "suspected_optout", "Suspected opt-out"
    BOUNCE_HARD = "bounce_hard", "Hard bounce"
    BOUNCE_SOFT = "bounce_soft", "Soft bounce"
    COMPLAINT = "complaint", "Spam complaint"


class QuarantineReason(models.TextChoices):
    OVERSIZED = "oversized", "Oversized"
    UNPARSEABLE = "unparseable", "Unparseable"
    DATA_ERROR = "data_error", "Data error"


class MailboxAuth(models.TextChoices):
    PASSWORD = "password", "Password"


class SmtpSecurity(models.TextChoices):
    STARTTLS = "starttls", "STARTTLS"
    SSL = "ssl", "SSL/TLS"


class MailboxState(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    NEEDS_LOGIN = "needs_login", "Needs login"
    DISABLED = "disabled", "Disabled"


class PauseAction(models.TextChoices):
    PAUSE = "pause", "Pause"
    RESUME = "resume", "Resume"


class HealthTrigger(models.TextChoices):
    BOUNCE = "bounce", "Hard bounces"
    OPTOUT = "optout", "Opt-outs"
    COMPLAINT = "complaint", "Spam complaints"
    MANUAL = "manual", "Manual"


class ReplyMatch(models.TextChoices):
    HEADER = "header", "Header"
    SENDER = "sender", "Sender"
    DSN = "dsn", "DSN"
    FEEDBACK = "feedback", "Feedback report"


class SequenceStopReason(models.TextChoices):
    REPLIED = "replied", "Replied"
    OPTOUT = "optout", "Opt-out"
    BOUNCE = "bounce", "Bounce"
    MANUAL = "manual", "Manual"
    FINISHED = "finished", "Finished"
    PAUSED = "paused", "Paused"
    FAILED = "failed", "Failed"
    SUPPRESSED = "suppressed", "Suppressed"
    COMPLAINT = "complaint", "Spam complaint"


class ReplyIntent(models.TextChoices):
    """The class of a human reply, given by the AI toolbox; advice for a person, never a decision."""

    INTERESTED = "interested", "Interested"
    NOT_NOW = "not_now", "Not now"
    NOT_INTERESTED = "not_interested", "Not interested"
    OUT_OF_OFFICE = "out_of_office", "Out of office"
    WRONG_PERSON = "wrong_person", "Wrong person"
    UNSUBSCRIBE = "unsubscribe", "Unsubscribe"
    UNCLEAR = "unclear", "Unclear"


class IntentSource(models.TextChoices):
    TOOLBOX = "toolbox", "AI toolbox"


class ReplySequenceAction(models.TextChoices):
    """What a classified reply does to the thread's sequence."""

    STOP = "stop", "Stop"
    PAUSE = "pause", "Pause"
    PAUSE_UNTIL_RETURN = "pause_until_return", "Pause until the return date"


class AlertSeverity(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


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
