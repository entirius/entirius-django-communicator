# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Inbound mail → `Reply`. Fixed order: duplicate → DSN → thread match → autoresponder → opt-out → reply.

The only writer of `Thread.status` besides the plan 05 close action. Unmatched mail is dropped, not stored.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage

from django.db import transaction
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import (
    MESSAGE_STATUS_TRANSITIONS,
    Direction,
    FailureCode,
    MessageStatus,
    ReplyKind,
    ReplyMatch,
    SequenceStopReason,
    ThreadStatus,
)
from django_communicator.models import Channel, Message, Reply, Thread
from django_communicator.services import (
    alert_service,
    clock_service,
    dsn_service,
    mail_parser,
    message_service,
    sequence_service,
    suppression_service,
)
from django_communicator.signals import reply_received

logger = logging.getLogger(__name__)

OPTOUT_SCAN_CHARS = 2000
AUTO_PRECEDENCE = frozenset({"auto_reply", "bulk", "junk"})


@dataclass(frozen=True)
class Inbound:
    msg: EmailMessage
    message_id: str


@dataclass(frozen=True)
class Match:
    thread: Thread
    message: Message | None
    matched_by: str


@transaction.atomic
def ingest(channel: Channel, raw: bytes) -> Reply | None:
    """Store one inbound mail; the existing `Reply` for an already seen Message-ID, no side effects (C-26)."""
    msg = mail_parser.parse(raw)
    inbound = Inbound(msg=msg, message_id=mail_parser.inbound_message_id(msg, raw))
    existing = Reply.objects.filter(inbound_message_id=inbound.message_id).first()
    if existing is not None:
        return existing
    if _outbound_by_ids(channel, [inbound.message_id]) is not None:
        return None  # our own mail, e.g. a sandbox copy in the polled mailbox
    if dsn_service.is_dsn(msg):
        return _ingest_dsn(channel, inbound)
    match = _match_thread(channel, msg)
    if match is None:
        logger.info("communicator inbound %s in channel %s matched no thread", inbound.message_id, channel.idx)
        return None
    return _classify(channel, inbound, match)


def _outbound_by_ids(channel: Channel, ids: list[str]) -> Message | None:
    if not ids:
        return None
    messages = Message.objects.select_related("thread__recipient_language").filter(
        thread__channel=channel, direction=Direction.OUT, message_id__in=ids
    )
    return messages.order_by("-pk").first()


def _match_thread(channel: Channel, msg: EmailMessage) -> Match | None:
    """Our Message-ID in In-Reply-To/References (C-20), else the newest open thread of the sender (C-21)."""
    message = _outbound_by_ids(
        channel, mail_parser.header_ids(msg, "In-Reply-To") + mail_parser.header_ids(msg, "References")
    )
    if message is not None:
        return Match(thread=message.thread, message=message, matched_by=ReplyMatch.HEADER)
    sender = mail_parser.sender(msg)
    threads = Thread.objects.select_related("recipient_language").filter(
        channel=channel, status=ThreadStatus.OPEN, recipient_email__iexact=sender
    )
    thread = threads.order_by("-created_at", "-pk").first() if sender else None
    return Match(thread=thread, message=None, matched_by=ReplyMatch.SENDER) if thread else None


def _classify(channel: Channel, inbound: Inbound, match: Match) -> Reply:
    if is_autoreply(inbound.msg):
        return _store(inbound, match, ReplyKind.AUTO)  # C-22: sequence, thread and notifications untouched
    body = mail_parser.body_text(inbound.msg)
    if has_optout_phrase(match.thread, body):
        return _suspected_optout(channel, inbound, match, body)
    return _plain_reply(channel, inbound, match, body)


def is_autoreply(msg: EmailMessage) -> bool:
    if str(msg.get("Auto-Submitted", "") or "no").strip().lower() != "no":
        return True
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    if str(msg.get("Precedence", "")).strip().lower() in AUTO_PRECEDENCE:
        return True
    subject = mail_parser.subject(msg).lower()
    return any(pattern in subject for pattern in communicator_settings.COMMUNICATOR_AUTOREPLY_SUBJECT_PATTERNS)


def has_optout_phrase(thread: Thread, body: str) -> bool:
    """Phrases of the recipient's language; every language when the thread has none with a list."""
    phrases = communicator_settings.COMMUNICATOR_OPTOUT_PHRASES
    language = thread.recipient_language.iso2.lower() if thread.recipient_language else ""
    candidates = phrases.get(language) or [phrase for group in phrases.values() for phrase in group]
    text = body[:OPTOUT_SCAN_CHARS].lower()
    return any(phrase.lower() in text for phrase in candidates)


def _store(inbound: Inbound, match: Match, kind: str, body: str | None = None) -> Reply:
    msg = inbound.msg
    return Reply.objects.create(
        thread=match.thread,
        message=match.message,
        from_email=mail_parser.sender(msg),
        subject=mail_parser.subject(msg),
        body_text=mail_parser.body_text(msg) if body is None else body,
        kind=kind,
        matched_by=match.matched_by,
        inbound_message_id=inbound.message_id,
        received_at=timezone.now(),
        raw_headers=mail_parser.headers(msg),
    )


def _suspected_optout(channel: Channel, inbound: Inbound, match: Match, body: str) -> Reply:
    """C-23: a suspicion only — the sequence pauses until a human confirms or dismisses; no suppression yet."""
    reply = _store(inbound, match, ReplyKind.SUSPECTED_OPTOUT, body)
    sequence_service.pause_sequence(match.thread)
    _mark_replied(match.thread, reply)
    alert_service.notify_subject(
        channel, subject_ref=match.thread.subject_ref, severity="medium", title="Possible opt-out"
    )
    return reply


def _plain_reply(channel: Channel, inbound: Inbound, match: Match, body: str) -> Reply:
    reply = _store(inbound, match, ReplyKind.REPLY, body)
    _mark_replied(match.thread, reply)
    mark_message_replied(reply)
    sequence_service.stop_sequence(match.thread, SequenceStopReason.REPLIED)
    transaction.on_commit(lambda: reply_received.send(sender=Thread, thread=match.thread, reply=reply))
    title = f"Reply from {reply.from_email}"
    alert_service.notify_subject(channel, subject_ref=match.thread.subject_ref, severity="high", title=title)
    return reply


def _mark_replied(thread: Thread, reply: Reply) -> None:
    thread.status, thread.last_message_at = ThreadStatus.REPLIED, reply.received_at
    Thread.objects.filter(pk=thread.pk).update(
        status=thread.status, last_message_at=thread.last_message_at, modified_at=timezone.now()
    )


def mark_message_replied(reply: Reply) -> None:
    """`Message.replied_at` of the outbound message a header-matched reply answers."""
    if reply.message_id is not None:
        Message.objects.filter(pk=reply.message_id, replied_at=None).update(replied_at=reply.received_at)


def _ingest_dsn(channel: Channel, inbound: Inbound) -> Reply | None:
    dsn = dsn_service.parse(inbound.msg)
    message = _outbound_by_ids(channel, [dsn.original_message_id] if dsn.original_message_id else [])
    if message is None or not (dsn.is_hard or dsn.is_soft):
        logger.info("communicator DSN %s (status %r) matched no message", inbound.message_id, dsn.status)
        return None
    match = Match(thread=message.thread, message=message, matched_by=ReplyMatch.DSN)
    if dsn.is_hard:
        return _hard_bounce(channel, inbound, match, dsn)
    return _soft_bounce(channel, inbound, match, dsn)


def _hard_bounce(channel: Channel, inbound: Inbound, match: Match, dsn: dsn_service.Dsn) -> Reply:
    """C-24: message failed(bounce), address suppressed, sequence stopped, low notification. Thread unchanged."""
    reply = _store(inbound, match, ReplyKind.BOUNCE_HARD)
    _fail_bounced(match.message, dsn)
    recipient = dsn.final_recipient or match.thread.recipient_email
    suppression_service.suppress_email(channel, recipient, reason=f"dsn {dsn.status}")
    sequence_service.stop_sequence(match.thread, SequenceStopReason.BOUNCE)
    alert_service.notify_subject(channel, subject_ref=match.thread.subject_ref, severity="low", title="Bounce")
    return reply


def _soft_bounce(channel: Channel, inbound: Inbound, match: Match, dsn: dsn_service.Dsn) -> Reply:
    """C-25: the first soft bounce retries after COMMUNICATOR_SOFT_BOUNCE_RETRY_H, the second fails."""
    reply = _store(inbound, match, ReplyKind.BOUNCE_SOFT)
    message = match.message
    if message.bounce_retry_at is not None:
        _fail_bounced(message, dsn)
        return reply
    retry_at = clock_service.now_for(channel) + timedelta(hours=communicator_settings.COMMUNICATOR_SOFT_BOUNCE_RETRY_H)
    _transition_logged(message, MessageStatus.SCHEDULED, {"bounce_retry_at": retry_at, "scheduled_at": retry_at})
    return reply


def _fail_bounced(message: Message, dsn: dsn_service.Dsn) -> None:
    fields = {"failure_code": FailureCode.BOUNCE, "failure_detail": f"dsn {dsn.status}"}
    _transition_logged(message, MessageStatus.FAILED, fields)


def _transition_logged(message: Message, status: str, fields: dict) -> None:
    """A DSN for a message that already moved on (e.g. failed twice) keeps the stored status."""
    if status not in MESSAGE_STATUS_TRANSITIONS.get(message.status, frozenset()):
        logger.info("communicator DSN leaves message %s in %s", message.pk, message.status)
        return
    message_service.transition(message, status, fields=fields)
