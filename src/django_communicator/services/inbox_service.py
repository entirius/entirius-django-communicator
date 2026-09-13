# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Read side of the inbound flow for the admin API: threads with their timeline, replies, mailbox config."""

from django.db.models import QuerySet

from django_communicator.enums import Direction, MessageStatus
from django_communicator.models import Channel, MailboxConfig, Message, Reply, Thread

TIMELINE_HIDDEN_STATUSES = (MessageStatus.SUPERSEDED, MessageStatus.REJECTED)
MAILBOX_IDENTITY = ("imap_host", "imap_user", "folder")


def list_threads(channel: Channel, *, subject_ref: str | None = None) -> QuerySet[Thread]:
    threads = Thread.objects.filter(channel=channel)
    if subject_ref:
        threads = threads.filter(subject_ref=subject_ref)
    return threads.order_by("-created_at", "-pk")


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
