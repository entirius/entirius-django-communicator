# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Delivery of one due message in its channel mode. Called only by `send_service` (the `send_due` beat).

Send-once: the message is claimed `sending` in a committed transaction, SMTP runs outside any transaction and a
second short transaction records the outcome. A claim that never got its outcome is not re-sent: after
`COMMUNICATOR_SENDING_STALE_MINUTES` it ends `failed/send_outcome_unknown`.

dry_run → `would_send`, no SMTP. sandbox → the sandbox mailbox. live → the recipient, only while the live double
gate holds for the re-read channel. SMTP 5xx → `failed/smtp` (+ suppression of the recipient only when RCPT was
refused with 550/551/553/554 in live); 4xx and transport errors → `scheduled` for the next run, `failed/smtp`
after `COMMUNICATOR_SMTP_MAX_ATTEMPTS`.
"""

import logging
import smtplib
from datetime import datetime, timedelta

from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import ChannelMode, FailureCode, MessageStatus, SuppressionKind
from django_communicator.models import Channel, Message
from django_communicator.services import (
    alert_service,
    channel_service,
    mail_builder,
    message_service,
    sequence_service,
    suppression_service,
)
from django_communicator.signals import message_sent

logger = logging.getLogger(__name__)

DEFERRED = "deferred"
HARD_BOUNCE_CODES = frozenset({550, 551, 553, 554})
LIVE_REFUSED = "Live sending refused"


def deliver(message: Message, *, now: datetime) -> str:
    """Outcome: `sent`, `would_send`, `suppressed`, `skipped`, `failed` or `deferred`.

    Suppression, follow-up blocker and mail build run before the claim; an error after the claim but before SMTP
    releases it. Raises `SmtpNotConfiguredError` (nothing claimed).
    """
    current = Message.objects.select_related("thread__channel").get(pk=message.pk)
    if current.status not in (MessageStatus.APPROVED, MessageStatus.SCHEDULED):
        return DEFERRED
    refusal = _refusal(current)
    mail = None if refusal or current.thread.channel.mode == ChannelMode.DRY_RUN else mail_builder.build(current)
    claimed = message_service.claim_for_sending(current)
    if claimed is None:
        return DEFERRED
    try:
        outcome = _before_smtp(claimed, current, refusal, now)
    except Exception:
        message_service.release_claim(claimed, current.status)
        raise
    return outcome or _send(claimed, mail, now)


def _before_smtp(claimed: Message, current: Message, refusal: tuple[str, dict] | None, now: datetime) -> str | None:
    """The outcome when the claimed message must not reach SMTP; None to send the mail built before the claim."""
    channel, built_for = claimed.thread.channel, current.thread.channel
    if channel.mode == ChannelMode.LIVE and not channel_service.live_allowed(channel):
        message_service.release_claim(claimed, current.status)
        refuse_live(channel, now)
        return DEFERRED
    if (channel.mode, channel.sandbox_mailbox) != (built_for.mode, built_for.sandbox_mailbox):
        message_service.release_claim(claimed, current.status)
        return DEFERRED
    if refusal is not None:
        return _finish(claimed, *refusal)
    if channel.mode == ChannelMode.DRY_RUN:
        return _finish(claimed, MessageStatus.WOULD_SEND, {"sent_at": now})
    return None


def refuse_live(channel: Channel, now: datetime) -> None:
    alert_service.notify_once(channel, kind="live_refused", severity="critical", title=LIVE_REFUSED, day=now.date())


def _refusal(message: Message) -> tuple[str, dict] | None:
    """Suppressed since approval, or a follow-up whose thread got a reply or whose sequence no longer runs."""
    thread = message.thread
    if suppression_service.is_suppressed(thread.channel, thread.recipient_email):
        return MessageStatus.SUPPRESSED, {}
    blocker = sequence_service.follow_up_blocker(message)
    return (MessageStatus.SKIPPED, {"failure_detail": blocker}) if blocker else None


def _finish(message: Message, outcome: str, fields: dict) -> str:
    with transaction.atomic():
        message_service.transition(message, outcome, fields=fields)
        sequence_service.on_follow_up_finished(message, outcome)
    return outcome


def _send(message: Message, mail: EmailMultiAlternatives, now: datetime) -> str:
    try:
        mail.send()
    except (smtplib.SMTPException, OSError) as error:
        return _smtp_error(message, error, now)
    attempts = message.send_attempts + 1
    fields = {"sent_at": now, "message_id": mail.extra_headers["Message-ID"], "send_attempts": attempts}
    _finish(message, MessageStatus.SENT, fields)
    transaction.on_commit(lambda: message_sent.send(sender=Message, message=message))
    return MessageStatus.SENT


def fail_stale_sending(channel: Channel) -> int:
    """`sending` claims older than the stale limit end `failed/send_outcome_unknown` — never sent again."""
    cutoff = timezone.now() - timedelta(minutes=communicator_settings.COMMUNICATOR_SENDING_STALE_MINUTES)
    stale = Message.objects.select_related("thread__channel").filter(
        thread__channel=channel, status=MessageStatus.SENDING, send_attempted_at__lt=cutoff
    )
    failed = 0
    for message in stale:
        logger.warning("communicator message %s: send outcome unknown, marked failed", message.pk)
        try:
            _finish(message, MessageStatus.FAILED, {"failure_code": FailureCode.SEND_OUTCOME_UNKNOWN})
        except message_service.InvalidTransitionError:
            continue
        failed += 1
    return failed


def smtp_code(error: Exception) -> int:
    """The SMTP reply code of the error; 0 for transport errors without one."""
    if isinstance(error, smtplib.SMTPResponseException):
        return error.smtp_code
    if isinstance(error, smtplib.SMTPRecipientsRefused) and error.recipients:
        return next(iter(error.recipients.values()))[0]
    return 0


def _smtp_error(message: Message, error: Exception, now: datetime) -> str:
    code = smtp_code(error)
    attempts = message.send_attempts + 1
    logger.warning(
        "communicator message %s: SMTP error %s (attempt %s)", message.pk, code or type(error).__name__, attempts
    )
    if code >= 500 or attempts >= communicator_settings.COMMUNICATOR_SMTP_MAX_ATTEMPTS:
        _fail(message, error, attempts, now)
        return MessageStatus.FAILED
    message_service.transition(message, MessageStatus.SCHEDULED, fields={"send_attempts": attempts})
    return DEFERRED


def _fail(message: Message, error: Exception, attempts: int, now: datetime) -> None:
    code = smtp_code(error)
    fields = {"failure_code": FailureCode.SMTP, "failure_detail": str(code), "send_attempts": attempts}
    _finish(message, MessageStatus.FAILED, fields)
    channel = message.thread.channel
    if isinstance(error, smtplib.SMTPSenderRefused | smtplib.SMTPDataError):
        title = "SMTP refused the sender or the message"
        alert_service.notify_once(channel, kind="smtp_rejected", severity="high", title=title, day=now.date())
        return
    if isinstance(error, smtplib.SMTPRecipientsRefused) and code in HARD_BOUNCE_CODES:
        _suppress_live_recipient(message, code)


def _suppress_live_recipient(message: Message, code: int) -> None:
    channel = message.thread.channel
    if channel.mode != ChannelMode.LIVE:
        return
    try:
        suppression_service.create_suppression(
            channel, kind=SuppressionKind.EMAIL, value=message.thread.recipient_email, reason=f"smtp {code}"
        )
    except (ValueError, suppression_service.DuplicateSuppressionError):
        pass
