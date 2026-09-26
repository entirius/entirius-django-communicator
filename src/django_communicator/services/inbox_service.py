# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Read side of the inbound flow for the admin API: threads with their timeline, replies, mailbox config."""

from collections import defaultdict
from itertools import chain

from django.db.models import Exists, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import Coalesce, Greatest

from django_communicator.enums import Direction, MessageStatus, ThreadStatus
from django_communicator.models import Channel, MailboxConfig, Message, Reply, SendPolicy, Thread
from django_communicator.services.send_service import DUE_STATUSES, next_slot_for

TIMELINE_HIDDEN_STATUSES = (MessageStatus.SUPERSEDED, MessageStatus.REJECTED)
MAILBOX_IDENTITY = ("imap_host", "imap_user", "folder")
# What an inbox list filters on: a draft waiting for review, a mail waiting for the send beat, a reply.
THREAD_STATES = ("draft", "waiting", "replied")
THREAD_SORTS = ("created", "activity")
LAST_TEXT_MAX = 300


def list_threads(
    channel: Channel, *, subject_ref: str | None = None, state: str | None = None, sort: str = "created"
) -> QuerySet[Thread]:
    """Newest created first (a caller's "newest thread"), or `sort="activity"`: the latest mail, draft or reply first."""
    threads = _scoped(channel, subject_ref)
    if state:
        threads = threads.filter(_state_filter(state))
    if sort == "activity":
        latest_message = Message.objects.filter(thread=OuterRef("pk")).order_by("-created_at").values("created_at")[:1]
        activity = Greatest(Coalesce("last_message_at", "created_at"), Coalesce(Subquery(latest_message), "created_at"))
        return threads.annotate(activity_at=activity).order_by("-activity_at", "-pk")
    return threads.order_by("-created_at", "-pk")


def count_states(channel: Channel, *, subject_ref: str | None = None) -> dict[str, int]:
    """Threads per inbox state; a thread may count in several (a reply and a waiting answer)."""
    threads = _scoped(channel, subject_ref)
    return {"all": threads.count(), **{state: threads.filter(_state_filter(state)).count() for state in THREAD_STATES}}


def thread_rows(threads: list[Thread], policy: SendPolicy | None) -> list[dict]:
    """What an inbox row shows for each thread of a page — two queries for the whole page."""
    ids = [thread.pk for thread in threads]
    messages, replies = defaultdict(list), defaultdict(list)
    visible = Message.objects.filter(thread_id__in=ids).exclude(status__in=TIMELINE_HIDDEN_STATUSES)
    for message in visible.order_by("created_at", "pk"):
        messages[message.thread_id].append(message)
    for reply in Reply.objects.filter(thread_id__in=ids).order_by("received_at", "pk"):
        replies[reply.thread_id].append(reply)
    return [_row(thread, messages[thread.pk], replies[thread.pk], policy) for thread in threads]


def _scoped(channel: Channel, subject_ref: str | None) -> QuerySet[Thread]:
    threads = Thread.objects.filter(channel=channel)
    return threads.filter(subject_ref=subject_ref) if subject_ref else threads


def _state_filter(state: str) -> Q:
    if state == "replied":
        return Q(status=ThreadStatus.REPLIED)
    statuses = (MessageStatus.REVIEW_REQUIRED,) if state == "draft" else DUE_STATUSES
    return Q(Exists(Message.objects.filter(thread=OuterRef("pk"), status__in=statuses)))


def _row(thread: Thread, messages: list[Message], replies: list[Reply], policy: SendPolicy | None) -> dict:
    outbound = next((message for message in messages if message.direction == Direction.OUT), None)
    entries = sorted(
        chain(((m.created_at, m.body_text) for m in messages), ((r.received_at, r.body_text) for r in replies)),
        key=lambda entry: entry[0],
    )
    draft = next((m for m in reversed(messages) if m.status == MessageStatus.REVIEW_REQUIRED), None)
    waiting = next((m for m in messages if m.status in DUE_STATUSES), None)
    return {
        "thread": thread,
        "activity_at": getattr(thread, "activity_at", None) or thread.last_message_at or thread.created_at,
        "subject": outbound.subject if outbound else (replies[0].subject if replies else ""),
        "last_text": entries[-1][1][:LAST_TEXT_MAX] if entries else "",
        "draft": {"id": draft.pk, "subject": draft.subject} if draft else None,
        "waiting": _waiting(waiting, policy) if waiting else None,
    }


def _waiting(message: Message, policy: SendPolicy | None) -> dict:
    return {
        "id": message.pk,
        "status": message.status,
        "scheduled_at": message.scheduled_at,
        "next_slot": next_slot_for(message, policy),
    }


def get_thread(channel_idx: str, pk: int) -> Thread:
    """One query (channel and sequence state joined). Raises `Thread.DoesNotExist`."""
    return Thread.objects.select_related("sequence_state").get(channel__idx=channel_idx, pk=pk)


def timeline(thread: Thread) -> list[dict]:
    """Messages (without superseded/rejected versions) and replies, oldest first — two queries."""
    messages = Message.objects.filter(thread=thread).exclude(status__in=TIMELINE_HIDDEN_STATUSES)
    entries = [_message_entry(message) for message in messages]
    entries += [_reply_entry(reply) for reply in Reply.objects.filter(thread=thread)]
    return sorted(entries, key=lambda entry: entry["at"])


def _message_entry(message: Message) -> dict:
    return {
        "kind": "message",
        "message_id": message.pk,
        "at": message.sent_at or message.created_at,
        "direction": message.direction,
        "status": message.status,
        "subject": message.subject,
        "body_text": message.body_text,
        "from_email": "",
        "reply_kind": "",
    }


def _reply_entry(reply: Reply) -> dict:
    return {
        "kind": "reply",
        "message_id": None,
        "at": reply.received_at,
        "direction": Direction.IN,
        "status": "",
        "subject": reply.subject,
        "body_text": reply.body_text,
        "from_email": reply.from_email,
        "reply_kind": reply.kind,
    }


def list_replies(channel: Channel, *, kind: str | None = None, thread_id: int | None = None) -> QuerySet[Reply]:
    replies = Reply.objects.filter(thread__channel=channel)
    if kind:
        replies = replies.filter(kind=kind)
    if thread_id is not None:
        replies = replies.filter(thread_id=thread_id)
    return replies.order_by("-received_at", "-pk")


def get_reply(channel: Channel, pk: int) -> Reply:
    """Raises `Reply.DoesNotExist` when the reply is not in this channel."""
    return Reply.objects.get(thread__channel=channel, pk=pk)


def get_mailbox(channel: Channel) -> MailboxConfig | None:
    return MailboxConfig.objects.filter(channel=channel).first()


def save_mailbox(channel: Channel, *, imap_password: str | None = None, **fields) -> MailboxConfig:
    """Create or update the channel's mailbox; a missing password keeps the stored one.

    Another host, user or folder is another mailbox: the cursor and its UIDVALIDITY start over.
    """
    config = get_mailbox(channel) or MailboxConfig(channel=channel)
    identity = [getattr(config, name) for name in MAILBOX_IDENTITY]
    for name, value in fields.items():
        setattr(config, name, value)
    if config.pk and identity != [getattr(config, name) for name in MAILBOX_IDENTITY]:
        config.last_uid, config.uid_validity = 0, None
    if imap_password is not None:
        config.imap_password = imap_password
    config.save()
    return config
