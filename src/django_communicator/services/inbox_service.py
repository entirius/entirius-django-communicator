# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Read side of the inbound flow for the admin API: threads with their timeline, replies, mailbox config."""

from collections import defaultdict
from itertools import chain

from django.db.models import Count, Exists, OuterRef, Q, QuerySet, Subquery
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
        return threads.annotate(activity_at=_activity()).order_by("-activity_at", "-pk")
    return threads.order_by("-created_at", "-pk")


def list_conversations(channel: Channel, *, state: str | None = None) -> QuerySet[Thread]:
    """The newest thread of every `subject_ref` (a conversation), latest activity of the conversation first.

    Each carries `activity_at` and `thread_count` of its conversation; `state` holds when any of its threads is in it.
    """
    threads = _scoped(channel, None)
    newest = threads.order_by("subject_ref", "-created_at", "-pk").distinct("subject_ref").values("pk")
    conversations = Thread.objects.filter(pk__in=newest)
    if state:
        conversations = conversations.filter(subject_ref__in=threads.filter(_state_filter(state)).values("subject_ref"))
    siblings = threads.filter(subject_ref=OuterRef("subject_ref")).order_by()
    activity = siblings.annotate(at=_activity()).order_by("-at").values("at")[:1]
    thread_count = siblings.values("subject_ref").annotate(total=Count("pk")).values("total")
    annotated = conversations.annotate(activity_at=Subquery(activity), thread_count=Subquery(thread_count))
    return annotated.order_by("-activity_at", "-pk")


def count_conversations(channel: Channel) -> dict[str, int]:
    """Conversations per inbox state — a conversation counts in a state when any of its threads is in it."""
    threads = _scoped(channel, None).order_by()

    def total(queryset: QuerySet[Thread]) -> int:
        return queryset.values("subject_ref").distinct().count()

    return {"all": total(threads), **{state: total(threads.filter(_state_filter(state))) for state in THREAD_STATES}}


def count_states(channel: Channel, *, subject_ref: str | None = None) -> dict[str, int]:
    """Threads per inbox state; a thread may count in several (a reply and a waiting answer)."""
    threads = _scoped(channel, subject_ref)
    return {"all": threads.count(), **{state: threads.filter(_state_filter(state)).count() for state in THREAD_STATES}}


def thread_rows(threads: list[Thread], policy: SendPolicy | None) -> list[dict]:
    """What an inbox row shows for each thread of a page — two queries for the whole page."""
    messages, replies = _events([thread.pk for thread in threads])
    return [_row(thread, messages[thread.pk], replies[thread.pk], policy) for thread in threads]


def conversation_rows(conversations: list[Thread], policy: SendPolicy | None) -> list[dict]:
    """A thread row of each conversation's newest thread, its state taken from every thread — three queries a page.

    Subject is the newest thread's; `last_text`, `draft` and `waiting` come from any thread, so a draft in an older
    thread is the one the row opens.
    """
    if not conversations:
        return []
    siblings = _siblings(conversations)
    ref_of = {pk: subject_ref for pk, subject_ref, *_ in siblings}
    replied = {subject_ref for _, subject_ref, status, _ in siblings if status == ThreadStatus.REPLIED}
    messages, replies = _events(list(ref_of))
    by_ref = _merged(messages, ref_of, key=lambda m: (m.created_at, m.pk))
    replies_by_ref = _merged(replies, ref_of, key=lambda r: (r.received_at, r.pk))
    recipients = {pk: email for pk, *_, email in siblings}  # a draft may sit in an older thread, to another recipient
    recipient_of = {m.pk: recipients[m.thread_id] for m in chain.from_iterable(messages.values())}
    rows = [
        _row(newest, by_ref[newest.subject_ref], replies_by_ref[newest.subject_ref], policy) for newest in conversations
    ]
    return [
        row
        | {
            "draft": row["draft"] and row["draft"] | {"recipient_email": recipient_of[row["draft"]["id"]]},
            "subject": _subject(messages[newest.pk], replies[newest.pk]),
            "thread_count": newest.thread_count,
            "replied": newest.subject_ref in replied,
        }
        for newest, row in zip(conversations, rows, strict=True)
    ]


def _siblings(conversations: list[Thread]) -> list[tuple[int, str, str, str]]:
    """Every thread of these conversations: (pk, subject_ref, status, recipient_email)."""
    refs = [conversation.subject_ref for conversation in conversations]
    threads = Thread.objects.filter(channel_id=conversations[0].channel_id, subject_ref__in=refs)
    return list(threads.values_list("pk", "subject_ref", "status", "recipient_email"))


def _merged(events: dict[int, list], ref_of: dict[int, str], key) -> dict[str, list]:
    """Per-thread event lists joined per conversation, oldest first."""
    merged = defaultdict(list)
    for thread_id, entries in events.items():
        merged[ref_of[thread_id]].extend(entries)
    return defaultdict(list, {ref: sorted(entries, key=key) for ref, entries in merged.items()})


def _events(ids: list[int]) -> tuple[dict[int, list[Message]], dict[int, list[Reply]]]:
    """Visible messages and replies of these threads, oldest first, by thread id — two queries."""
    messages, replies = defaultdict(list), defaultdict(list)
    visible = Message.objects.filter(thread_id__in=ids).exclude(status__in=TIMELINE_HIDDEN_STATUSES)
    for message in visible.order_by("created_at", "pk"):
        messages[message.thread_id].append(message)
    for reply in Reply.objects.filter(thread_id__in=ids).order_by("received_at", "pk"):
        replies[reply.thread_id].append(reply)
    return messages, replies


def _activity() -> Greatest:
    """A thread's latest mail, draft or reply."""
    latest_message = Message.objects.filter(thread=OuterRef("pk")).order_by("-created_at").values("created_at")[:1]
    return Greatest(Coalesce("last_message_at", "created_at"), Coalesce(Subquery(latest_message), "created_at"))


def _scoped(channel: Channel, subject_ref: str | None) -> QuerySet[Thread]:
    threads = Thread.objects.filter(channel=channel)
    return threads.filter(subject_ref=subject_ref) if subject_ref else threads


def _state_filter(state: str) -> Q:
    if state == "replied":
        return Q(status=ThreadStatus.REPLIED)
    statuses = (MessageStatus.REVIEW_REQUIRED,) if state == "draft" else DUE_STATUSES
    return Q(Exists(Message.objects.filter(thread=OuterRef("pk"), status__in=statuses)))


def _subject(messages: list[Message], replies: list[Reply]) -> str:
    outbound = next((message for message in messages if message.direction == Direction.OUT), None)
    return outbound.subject if outbound else (replies[0].subject if replies else "")


def _row(thread: Thread, messages: list[Message], replies: list[Reply], policy: SendPolicy | None) -> dict:
    entries = sorted(
        chain(((m.created_at, m.body_text) for m in messages), ((r.received_at, r.body_text) for r in replies)),
        key=lambda entry: entry[0],
    )
    draft = next((m for m in reversed(messages) if m.status == MessageStatus.REVIEW_REQUIRED), None)
    waiting = next((m for m in messages if m.status in DUE_STATUSES), None)
    return {
        "thread": thread,
        "activity_at": getattr(thread, "activity_at", None) or thread.last_message_at or thread.created_at,
        "subject": _subject(messages, replies),
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
