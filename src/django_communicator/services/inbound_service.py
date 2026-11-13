# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Inbound mail → `Reply`. Fixed order: duplicate → DSN → complaint → thread match → autoresponder → class /
opt-out → reply.

The only writer of `Thread.status` besides the plan 05 close action; a closed thread stays closed. Unmatched mail is
dropped, not stored. Dedup is per channel: the same mail in two channels' mailboxes is ingested for each.

A human reply may get a class from the AI toolbox (`reply_class_service`) before it is stored. That call runs outside
any transaction; the writes of one reply are atomic, and so are the DSN and complaint paths. Without a class the
phrase detection decides, as before.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid

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
    ReplyIntent,
    ReplyKind,
    ReplyMatch,
    SequenceStopReason,
    SuppressionSource,
    ThreadStatus,
)
from django_communicator.models import Channel, Message, Reply, Thread
from django_communicator.services import (
    address_service,
    alert_service,
    clock_service,
    dsn_service,
    mail_parser,
    message_service,
    reply_class_service,
    reply_plan_service,
    sequence_service,
    suppression_service,
    unsubscribe_service,
)
from django_communicator.services.reply_class_service import Classification
from django_communicator.signals import message_bounced

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


def ingest(channel: Channel, raw: bytes) -> Reply | None:
    """Store one inbound mail; the existing `Reply` for an already seen Message-ID, no side effects (C-26)."""
    msg = mail_parser.parse(raw)
    inbound = Inbound(msg=msg, message_id=mail_parser.inbound_message_id(msg, raw))
    existing = _existing(channel, inbound)
    if existing is not None:
        return existing
    if _outbound_by_ids(channel, [inbound.message_id]) is not None:
        return None  # our own mail, e.g. a sandbox copy in the polled mailbox
    if dsn_service.is_dsn(msg):
        return _ingest_dsn(channel, inbound)
    if communicator_settings.COMMUNICATOR_COMPLAINT_FEEDBACK and dsn_service.is_arf(msg):
        return _ingest_complaint(channel, inbound)
    match = _match_thread(channel, msg)
    if match is None:
        logger.info("communicator inbound %s in channel %s matched no thread", inbound.message_id, channel.idx)
        return None
    return _human_reply(channel, inbound, match)


def _existing(channel: Channel, inbound: Inbound) -> Reply | None:
    return Reply.objects.filter(channel=channel, inbound_message_id=inbound.message_id).first()


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


def _human_reply(channel: Channel, inbound: Inbound, match: Match) -> Reply:
    if is_autoreply(inbound.msg):
        with transaction.atomic():
            return _store(inbound, match, ReplyKind.AUTO)  # C-22: sequence, thread and notifications untouched
    body = mail_parser.body_text(inbound.msg)
    today = clock_service.now_for(channel).date()
    subject = mail_parser.subject(inbound.msg)
    classification = reply_class_service.classify(
        channel, subject=subject, body=mail_parser.unquoted(body), today=today
    )
    return _store_human_reply(channel, inbound, match, body, classification)


@transaction.atomic
def _store_human_reply(
    channel: Channel, inbound: Inbound, match: Match, body: str, classification: Classification | None
) -> Reply:
    """The writes of one human reply. A poll that stored the same mail while the toolbox answered wins."""
    existing = _existing(channel, inbound)
    if existing is not None:
        return existing
    match.thread.refresh_from_db(fields=["status"])  # a person may have closed it while the toolbox answered
    phrase = has_optout_phrase(match.thread, mail_parser.unquoted(body))
    if is_suspected_optout(classification, phrase):
        return _suspected_optout(channel, inbound, match, body, classification)
    return _plain_reply(channel, inbound, match, body, classification)


def ingest_with_class(thread: Thread, body: str, classification: Classification | None) -> Reply:
    """Development only: a human reply to the thread through the normal write path, with this class (or none)
    instead of a toolbox call. It answers the newest outbound message of the thread."""
    msg = EmailMessage()
    msg["From"], msg["Subject"] = thread.recipient_email, "Re: test reply"
    msg["Message-ID"] = make_msgid(domain="test-reply.invalid")
    msg.set_content(body)
    last = Message.objects.filter(thread=thread, direction=Direction.OUT).order_by("-pk").first()
    match = Match(thread=thread, message=last, matched_by=ReplyMatch.HEADER if last else ReplyMatch.SENDER)
    inbound = Inbound(msg=msg, message_id=msg["Message-ID"])
    return _store_human_reply(thread.channel, inbound, match, mail_parser.body_text(msg), classification)


def is_suspected_optout(classification: Classification | None, phrase: bool) -> bool:
    """`unsubscribe` always is; without a usable class (or `unclear`) the phrase decides; a confident other class
    with a phrase hit is one too (a person decides), except `not_interested`, whose refusal wins over the phrase."""
    if classification is None or classification.intent == ReplyIntent.UNCLEAR:
        return phrase
    if classification.intent == ReplyIntent.UNSUBSCRIBE:
        return True
    return phrase and classification.intent != ReplyIntent.NOT_INTERESTED


def is_autoreply(msg: EmailMessage) -> bool:
    if str(msg.get("Auto-Submitted", "") or "no").strip().lower() != "no":
        return True
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    if str(msg.get("Precedence", "")).strip().lower() in AUTO_PRECEDENCE:
        return True
    subject = mail_parser.subject(msg).lower()
    return any(pattern in subject for pattern in communicator_settings.COMMUNICATOR_AUTOREPLY_SUBJECT_PATTERNS)


def fold(text: str) -> str:
    """Lower case, no diacritics (`ł` too), every run of other characters one space — for phrase matching."""
    decomposed = unicodedata.normalize("NFKD", text.casefold().replace("ł", "l"))
    letters = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", letters).split())


def has_optout_phrase(thread: Thread, body: str) -> bool:
    """Phrases of the recipient's language (every language when it has no list) in the reply's unquoted text.

    Both sides are folded (`fold`), so "Proszę usunąć mnie" and "usunięcie mnie z listy" match their stems; a phrase
    is a stem, not a whole word. A reply is a suspicion only: a person confirms or dismisses it."""
    phrases = communicator_settings.COMMUNICATOR_OPTOUT_PHRASES
    language = thread.recipient_language.iso2.lower() if thread.recipient_language else ""
    candidates = phrases.get(language) or [phrase for group in phrases.values() for phrase in group]
    text = fold(_without_unsubscribe_lines(body)[:OPTOUT_SCAN_CHARS])
    return any(folded in text for phrase in candidates if (folded := fold(phrase)))


def _without_unsubscribe_lines(body: str) -> str:
    """Our own unsubscribe link quoted back without `>` (Outlook) is not the recipient's wording."""
    return "\n".join(line for line in body.splitlines() if unsubscribe_service.PATH_PREFIX not in line)


def _store(
    inbound: Inbound, match: Match, kind: str, body: str | None = None, classification: Classification | None = None
) -> Reply:
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
        **_class_fields(classification),
    )


def _class_fields(classification: Classification | None) -> dict:
    if classification is None:
        return {}
    return {
        "intent": classification.intent,
        "intent_confidence": classification.confidence,
        "intent_source": classification.source,
        "intent_return_date": classification.return_date,
    }


def _suspected_optout(
    channel: Channel, inbound: Inbound, match: Match, body: str, classification: Classification | None
) -> Reply:
    """C-23: a suspicion only — the sequence pauses until a human confirms or dismisses; no suppression yet, under
    any plan. The class `unsubscribe` takes its plan's alert and review flag; a phrase hit alone, what it always did."""
    reply = _store(inbound, match, ReplyKind.SUSPECTED_OPTOUT, body, classification)
    sequence_service.pause_sequence(match.thread)
    _mark_replied(match.thread, reply)
    by_class = reply.intent == ReplyIntent.UNSUBSCRIBE
    plan = reply_plan_service.plan_for(channel, reply.intent) if by_class else reply_plan_service.PHRASE_OPTOUT_PLAN
    reply_plan_service.record(reply, plan)
    reply_plan_service.notify(reply, plan, title=reply_plan_service.optout_title(reply.from_email))
    return reply


def _plain_reply(
    channel: Channel, inbound: Inbound, match: Match, body: str, classification: Classification | None
) -> Reply:
    reply = _store(inbound, match, ReplyKind.REPLY, body, classification)
    _mark_replied(match.thread, reply)
    mark_message_replied(reply)
    plan = reply_plan_service.plan_for(channel, reply.intent)
    reply_plan_service.apply(reply, plan)
    transaction.on_commit(lambda: reply_plan_service.announce(reply, plan))
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


@transaction.atomic
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
        email = match.thread.recipient_email
        suppression_service.suppress_email(channel, email, source=SuppressionSource.BOUNCE, reason=f"dsn {dsn.status}")
        address_service.mark_hard_bounce(email)
    sequence_service.stop_sequence(match.thread, SequenceStopReason.BOUNCE)
    alert_service.notify_subject(channel, subject_ref=match.thread.subject_ref, severity="low", title="Bounce")
    if channel.mode == ChannelMode.LIVE:  # a sandbox bounce says nothing about the real recipient
        _send_bounced(match.message)
    return reply


@transaction.atomic
def _ingest_complaint(channel: Channel, inbound: Inbound) -> Reply | None:
    """A spam complaint for one of our messages is stored; on a live channel the recipient is suppressed, the
    sequence stops, a high notification goes out and `message_bounced(reason="complaint")` follows on commit. A
    sandbox or dry-run complaint says nothing about the real recipient: stored only. The message status stays.
    `Original-Rcpt-To` is not compared: feedback loops redact it, and our Message-ID already names the recipient."""
    arf = dsn_service.parse_arf(inbound.msg)
    message = _outbound_by_ids(channel, [arf.original_message_id] if arf.original_message_id else [])
    if message is None or not arf.is_complaint:
        logger.info("communicator feedback report %s (%r) ignored", inbound.message_id, arf.feedback_type)
        return None
    match = Match(thread=message.thread, message=message, matched_by=ReplyMatch.FEEDBACK)
    reply = _store(inbound, match, ReplyKind.COMPLAINT)
    if channel.mode == ChannelMode.LIVE:
        suppression_service.suppress_email(
            channel, match.thread.recipient_email, source=SuppressionSource.COMPLAINT, reason="complaint"
        )
        sequence_service.stop_sequence(match.thread, SequenceStopReason.COMPLAINT)
        alert_service.notify_subject(
            channel, subject_ref=match.thread.subject_ref, severity="high", title="Spam complaint"
        )
        _send_bounced(message, reason="complaint")
    return reply


def _send_bounced(message: Message, reason: str = "") -> None:
    transaction.on_commit(lambda: message_bounced.send(sender=Message, message=message, hard=True, reason=reason))


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
