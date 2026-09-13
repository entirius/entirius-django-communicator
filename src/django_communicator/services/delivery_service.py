# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Delivery of one due message in its channel mode. Called only by `send_service` (the `send_due` beat).

dry_run → `would_send`, no SMTP, no counter. sandbox → the sandbox mailbox. live → the recipient.
SMTP 5xx → `failed/smtp` (+ suppression of the recipient on 550/551/553/554 in live); 4xx and transport
errors → `scheduled` for the next run, `failed/smtp` after `COMMUNICATOR_SMTP_MAX_ATTEMPTS`.
"""

import logging
import smtplib
from datetime import datetime

import redis
from django.db import transaction

from django_communicator import settings as communicator_settings
from django_communicator.enums import ChannelMode, FailureCode, MessageStatus, SuppressionKind
from django_communicator.models import Message
from django_communicator.services import (
    counter_service,
    mail_builder,
    message_service,
    sequence_service,
    suppression_service,
)
from django_communicator.signals import message_sent

logger = logging.getLogger(__name__)

HARD_BOUNCE_CODES = frozenset({550, 551, 553, 554})


def deliver(message: Message, *, now: datetime) -> str:
    """Outcome: `sent`, `would_send`, `suppressed`, `failed` or `deferred` (retry). Raises `SmtpNotConfiguredError`.

    The recipient is checked against the suppression list again: it may have been added after approval.
    """
    channel = message.thread.channel
    if suppression_service.is_suppressed(channel, message.thread.recipient_email):
        message_service.transition(message, MessageStatus.SUPPRESSED)
        return MessageStatus.SUPPRESSED
    if channel.mode == ChannelMode.DRY_RUN:
        message_service.transition(message, MessageStatus.WOULD_SEND, fields={"sent_at": now})
        sequence_service.on_delivered(message)
        return MessageStatus.WOULD_SEND
    mail = mail_builder.build(message)
    try:
        mail.send()
    except (smtplib.SMTPException, OSError) as error:
        return _smtp_error(message, error)
    fields = {
        "sent_at": now,
        "message_id": mail.extra_headers["Message-ID"],
        "send_attempts": message.send_attempts + 1,
    }
    message_service.transition(message, MessageStatus.SENT, fields=fields)
    _count(message, now)
    sequence_service.on_delivered(message)
    transaction.on_commit(lambda: message_sent.send(sender=Message, message=message))
    return MessageStatus.SENT


def _count(message: Message, now: datetime) -> None:
    """Best effort: a Redis failure after the mail left must not roll the `sent` status back."""
    try:
        counter_service.increment(message.thread.channel, now.date())
    except redis.RedisError:
        logger.exception("communicator message %s sent but not counted", message.pk)


def smtp_code(error: Exception) -> int:
    """The SMTP reply code of the error; 0 for transport errors without one."""
    if isinstance(error, smtplib.SMTPResponseException):
        return error.smtp_code
    if isinstance(error, smtplib.SMTPRecipientsRefused) and error.recipients:
        return next(iter(error.recipients.values()))[0]
    return 0


def _smtp_error(message: Message, error: Exception) -> str:
    code = smtp_code(error)
    attempts = message.send_attempts + 1
    logger.warning(
        "communicator message %s: SMTP error %s (attempt %s)", message.pk, code or type(error).__name__, attempts
    )
    if code >= 500 or attempts >= communicator_settings.COMMUNICATOR_SMTP_MAX_ATTEMPTS:
        _fail(message, code, attempts)
        return MessageStatus.FAILED
    if message.status == MessageStatus.SCHEDULED:
        Message.objects.filter(pk=message.pk).update(send_attempts=attempts)
        message.send_attempts = attempts
    else:
        message_service.transition(message, MessageStatus.SCHEDULED, fields={"send_attempts": attempts})
    return "deferred"


def _fail(message: Message, code: int, attempts: int) -> None:
    fields = {"failure_code": FailureCode.SMTP, "failure_detail": str(code), "send_attempts": attempts}
    message_service.transition(message, MessageStatus.FAILED, reason="smtp", fields=fields)
    channel = message.thread.channel
    if code not in HARD_BOUNCE_CODES or channel.mode != ChannelMode.LIVE:
        return
    try:
        suppression_service.create_suppression(
            channel, kind=SuppressionKind.EMAIL, value=message.thread.recipient_email, reason=f"smtp {code}"
        )
    except (ValueError, suppression_service.DuplicateSuppressionError):
        pass
