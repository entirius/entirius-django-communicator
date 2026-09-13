# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Inbound mail → `Reply`. Fixed order: duplicate → DSN → thread match → autoresponder → opt-out → reply.

The only writer of `Thread.status` besides the plan 05 close action; a closed thread stays closed. Unmatched mail is
dropped, not stored. Dedup is per channel: the same mail in two channels' mailboxes is ingested for each.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import (
    MESSAGE_STATUS_TRANSITIONS,
    ChannelMode,
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
NOTE_MAX_CHARS = 64
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
    existing = Reply.objects.filter(channel=channel, inbound_message_id=inbound.message_id).first()
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


def _outbound_by_ids(channel: Channel, ids: list[str], *, lock: bool = False) -> Message | None:
    """The newest outbound message of the channel with one of `ids`; `lock` holds its row for a status change."""
    if not ids:
        return None
    messages = Message.objects.select_related("thread__recipient_language").filter(
        thread__channel=channel, direction=Direction.OUT, message_id__in=ids
    )
    if lock:
        messages = messages.select_for_update(of=("self",))
    return messages.order_by("-pk").first()


def _match_thread(channel: Channel, msg: EmailMessage) -> Match | None:
    """Our Message-ID in In-Reply-To, then References (C-20), else the newest open thread of the sender (C-21)."""
    message = _outbound_by_ids(channel, mail_parser.header_ids(msg, "In-Reply-To")) or _outbound_by_ids(
        channel, mail_parser.header_ids(msg, "References")
    )
    if message is not None:
        return Match(thread=message.thread, message=message, matched_by=ReplyMatch.HEADER)
    sender = mail_parser.sender(msg)
    thread = _sender_thread(channel, sender, mail_parser.sent_at(msg) or timezone.now()) if sender else None
    return Match(thread=thread, message=None, matched_by=ReplyMatch.SENDER) if thread else None


def _sender_thread(channel: Channel, sender: str, written_at: datetime) -> Thread | None:
    """The newest open thread of the sender whose last outbound mail left before the inbound mail was written."""
    last_out = Max("messages__sent_at", filter=Q(messages__direction=Direction.OUT))
    threads = Thread.objects.select_related("recipient_language").filter(
        channel=channel, status=ThreadStatus.OPEN, recipient_email__iexact=sender
    )
    threads = threads.annotate(last_out=last_out).filter(last_out__lt=written_at)
    return threads.order_by("-created_at", "-pk").first()


def _classify(channel: Channel, inbound: Inbound, match: Match) -> Reply:
    if is_autoreply(inbound.msg):
        return _store(inbound, match, ReplyKind.AUTO)  # C-22: sequence, thread and notifications untouched
    body = mail_parser.body_text(inbound.msg)
    if has_optout_phrase(match.thread, mail_parser.unquoted(body)):
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
    """Phrases of the recipient's language (every language when it has no list) in the reply's unquoted text."""
    phrases = communicator_settings.COMMUNICATOR_OPTOUT_PHRASES
    language = thread.recipient_language.iso2.lower() if thread.recipient_language else ""
    candidates = phrases.get(language) or [phrase for group in phrases.values() for phrase in group]
    text = body[:OPTOUT_SCAN_CHARS].lower()
    return any(phrase.lower() in text for phrase in candidates)


def _store(inbound: Inbound, match: Match, kind: str, body: str | None = None) -> Reply:
    msg = inbound.msg
    body_limit = communicator_settings.COMMUNICATOR_INBOUND_BODY_MAX_CHARS
    return Reply.objects.create(
        channel_id=match.thread.channel_id,
        thread=match.thread,
        message=match.message,
        from_email=mail_parser.sender(msg),
        subject=mail_parser.subject(msg),
        body_text=mail_parser.clean(mail_parser.body_text(msg) if body is None else body, body_limit),
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
    """`replied`, unless a human closed the thread — then only `last_message_at` moves."""
    status = thread.status if thread.status == ThreadStatus.CLOSED else ThreadStatus.REPLIED
    thread.status, thread.last_message_at = status, reply.received_at
    Thread.objects.filter(pk=thread.pk).update(
        status=thread.status, last_message_at=thread.last_message_at, modified_at=timezone.now()
    )


def reopen(thread: Thread) -> None:
    """A human resumed the sequence after dismissing an opt-out: `replied` → `open`; a closed thread stays closed."""
    Thread.objects.filter(pk=thread.pk, status=ThreadStatus.REPLIED).update(
        status=ThreadStatus.OPEN, modified_at=timezone.now()
    )
    thread.refresh_from_db(fields=["status"])


def mark_message_replied(reply: Reply) -> None:
    """`Message.replied_at` of the outbound message a header-matched reply answers."""
    if reply.message_id is not None:
        Message.objects.filter(pk=reply.message_id, replied_at=None).update(replied_at=reply.received_at)


def _ingest_dsn(channel: Channel, inbound: Inbound) -> Reply | None:
    dsn = dsn_service.parse(inbound.msg)
    ids = [dsn.original_message_id] if dsn.original_message_id else []
    message = _outbound_by_ids(channel, ids, lock=True)  # a concurrent DSN must not see a stale bounce_retry_at
    if message is None or not (dsn.is_hard or dsn.is_soft):
        logger.info("communicator DSN %s (status %r) matched no message", inbound.message_id, dsn.status)
        return None
    if dsn.final_recipient and dsn.final_recipient != message.thread.recipient_email.lower():
        logger.info(
            "communicator DSN %s ignored: final recipient is not the recipient of message %s",
            inbound.message_id,
            message.pk,
        )
        return None
    match = Match(thread=message.thread, message=message, matched_by=ReplyMatch.DSN)
    if not dsn.is_failure:
        return _delivery_note(inbound, match, dsn)
    if dsn.is_hard:
        return _hard_bounce(channel, inbound, match, dsn)
    return _soft_bounce(channel, inbound, match, dsn)


def _delivery_note(inbound: Inbound, match: Match, dsn: dsn_service.Dsn) -> Reply:
    """`Action: delayed` (or another non-failure report) is noted on the message — its status never changes."""
    reply = _store(inbound, match, ReplyKind.BOUNCE_SOFT)
    note = mail_parser.clean(f"{dsn.action} {dsn.status}", NOTE_MAX_CHARS)
    Message.objects.filter(pk=match.message.pk).update(delivery_note=note, modified_at=timezone.now())
    return reply


def _hard_bounce(channel: Channel, inbound: Inbound, match: Match, dsn: dsn_service.Dsn) -> Reply:
    """C-24: message failed(bounce), sequence stopped, low notification; the thread recipient is suppressed only in
    live mode (as SMTP refusals in `delivery_service`). Thread unchanged."""
    reply = _store(inbound, match, ReplyKind.BOUNCE_HARD)
    _fail_bounced(match.message, dsn)
    if channel.mode == ChannelMode.LIVE:
        suppression_service.suppress_email(channel, match.thread.recipient_email, reason=f"dsn {dsn.status}")
    sequence_service.stop_sequence(match.thread, SequenceStopReason.BOUNCE)
    alert_service.notify_subject(channel, subject_ref=match.thread.subject_ref, severity="low", title="Bounce")
    return reply


def _soft_bounce(channel: Channel, inbound: Inbound, match: Match, dsn: dsn_service.Dsn) -> Reply:
    """C-25, `Action: failed` 4.x.x: the first schedules one retry after COMMUNICATOR_SOFT_BOUNCE_RETRY_H; another
    while that retry waits is ignored; one for the sent retry fails the message."""
    reply = _store(inbound, match, ReplyKind.BOUNCE_SOFT)
    message = match.message
    if message.bounce_retry_at is None:
        retry_at = clock_service.now_for(channel) + timedelta(
            hours=communicator_settings.COMMUNICATOR_SOFT_BOUNCE_RETRY_H
        )
        _transition_logged(message, MessageStatus.SCHEDULED, {"bounce_retry_at": retry_at, "scheduled_at": retry_at})
    elif message.status == MessageStatus.SENT:
        _fail_bounced(message, dsn)
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
