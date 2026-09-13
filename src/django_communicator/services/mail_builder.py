# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Outgoing mail of a message: multipart/alternative, our Message-ID, threading headers, channel mode addressing.

The SMTP connection is the channel's one from django_email; this module never reads SMTP credentials itself.
"""

import secrets
from email.utils import parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape
from django_email.domain import EmailDomain

from django_communicator import settings as communicator_settings
from django_communicator.enums import ChannelMode, MessageStatus
from django_communicator.models import Channel, Message

FOOTER_SEPARATOR = "\n\n-- \n"


class SmtpNotConfiguredError(Exception):
    """No `EMAIL_SMTP_CONFIGURATION_CHANNELS` entry for the channel — nothing is sent."""


def build(message: Message) -> EmailMultiAlternatives:
    channel = message.thread.channel
    connection = EmailDomain(channel_idx=channel.idx).get_channel_smtp_connection_if_exists()
    if connection is None:
        raise SmtpNotConfiguredError(f"no SMTP configuration for channel {channel.idx}")
    sender = from_email(channel)
    to, subject, headers = _addressing(message, channel)
    headers.update(_threading_headers(message))
    headers["Message-ID"] = (
        f"<communicator-{message.pk}-{secrets.token_hex(4)}@{parseaddr(sender)[1].rpartition('@')[2]}>"
    )
    mail = EmailMultiAlternatives(subject, _text(message), sender, to, headers=headers, connection=connection)
    mail.attach_alternative(_html(message), "text/html")
    return mail


def from_email(channel: Channel) -> str:
    entry = (getattr(settings, "EMAIL_SMTP_CONFIGURATION_CHANNELS", None) or {}).get(channel.idx) or {}
    return entry.get("DEFAULT_FROM_EMAIL") or settings.DEFAULT_FROM_EMAIL


def _addressing(message: Message, channel: Channel) -> tuple[list[str], str, dict[str, str]]:
    recipient = message.thread.recipient_email
    if channel.mode != ChannelMode.SANDBOX:
        return [recipient], message.subject, {}
    subject = communicator_settings.COMMUNICATOR_SANDBOX_SUBJECT_PREFIX + message.subject
    return [channel.sandbox_mailbox], subject, {"X-Original-To": recipient}


def _threading_headers(message: Message) -> dict[str, str]:
    """`In-Reply-To` / `References` from the Message-IDs of earlier sent messages in the thread (C-28)."""
    earlier = (
        Message.objects.filter(thread_id=message.thread_id, status=MessageStatus.SENT)
        .exclude(pk=message.pk)
        .exclude(message_id="")
        .order_by("sent_at", "pk")
        .values_list("message_id", flat=True)
    )
    ids = list(earlier)
    return {"In-Reply-To": ids[-1], "References": " ".join(ids)} if ids else {}


def _text(message: Message) -> str:
    footer = message.legal_footer.strip()
    return message.body_text + (FOOTER_SEPARATOR + footer if footer else "")


def _html(message: Message) -> str:
    body = message.body_html or "".join(
        f"<p>{escape(part).replace(chr(10), '<br>')}</p>" for part in message.body_text.split("\n\n") if part.strip()
    )
    footer = message.legal_footer.strip()
    return body + (f"<p>{escape(footer)}</p>" if footer else "")
