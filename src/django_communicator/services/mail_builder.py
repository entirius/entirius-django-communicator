# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Outgoing mail of a message: multipart/alternative, our Message-ID, threading headers, channel mode addressing.

A thread with a mailbox leaves from that mailbox (its From, CC, BCC, signature, brand footer and SMTP login, from
`mailbox_service`); without one the channel's SMTP entry of django_email and its `DEFAULT_FROM_EMAIL` are used, as
before mailboxes existed. Both parts run body → mailbox signature → footer → unsubscribe link.
"""

import secrets
from email.headerregistry import BaseHeader, HeaderRegistry
from email.policy import EmailPolicy
from email.utils import parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.base import BaseEmailBackend
from django.utils.html import escape
from django_email.domain import EmailDomain

from django_communicator import settings as communicator_settings
from django_communicator.enums import ChannelMode, MessageStatus
from django_communicator.models import Channel, Mailbox, MailFooter, Message, MessageTemplateVersion, Reply, Thread
from django_communicator.services import footer_service, mailbox_service, unsubscribe_service

FOOTER_SEPARATOR = "\n\n-- \n"


class _UnfoldedHeader(BaseHeader):
    """A header written as one line. Python's SMTP policy turns a header with a word longer than 78 characters (the
    signed unsubscribe URL) into RFC 2047 encoded words, which no mail client reads as a `List-Unsubscribe` URL."""

    max_count = None

    @classmethod
    def parse(cls, value, kwds):
        kwds["decoded"] = str(value)
        kwds["parse_tree"] = None

    def fold(self, *, policy):
        return f"{self.name}: {self}{policy.linesep}"


class _UnsubscribeMail(EmailMultiAlternatives):
    """Writes `List-Unsubscribe` unfolded; every other header keeps the default policy. A `compat32` message (Django
    before 6.0) never encodes the URL, so it is left as it is."""

    def message(self, *args, **kwargs):
        message = super().message(*args, **kwargs)
        value = message["List-Unsubscribe"]
        if value is None or not isinstance(message.policy, EmailPolicy):
            return message
        registry = HeaderRegistry()
        registry.map_to_type("list-unsubscribe", _UnfoldedHeader)
        policy = message.policy
        del message["List-Unsubscribe"]
        message.policy = policy.clone(header_factory=registry)
        message["List-Unsubscribe"] = str(value)
        message.policy = policy
        return message


class SmtpNotConfiguredError(Exception):
    """No `EMAIL_SMTP_CONFIGURATION_CHANNELS` entry for the channel — nothing is sent."""


def build(message: Message) -> EmailMultiAlternatives:
    """Also fills `message.footer_html` (unsaved) — the delivery stores it with the sent status. With
    `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` set the mail carries the RFC 8058 headers and a link in both parts.

    Raises `SmtpNotConfiguredError` (no mailbox, no channel entry) or `mailbox_service.MailboxLoginError`."""
    channel = message.thread.channel
    mailbox = mailbox_service.for_message(message)
    connection, sender = _transport(channel, mailbox)
    to, subject, headers = _addressing(message, channel)
    cc = _copies(mailbox.cc_addresses if mailbox else [], channel, headers, "X-Original-Cc")
    bcc = _copies(mailbox.bcc_addresses if mailbox else [], channel, headers, "X-Original-Bcc")
    headers.update(_threading_headers(message))
    headers["Message-ID"] = message.message_id or (  # a soft-bounce retry keeps the Message-ID it was sent with
        f"<communicator-{message.pk}-{secrets.token_hex(4)}@{parseaddr(sender)[1].rpartition('@')[2]}>"
    )
    link = unsubscribe_service.url_for(channel.idx, message.thread.recipient_email)
    message.footer_html = message.footer_html or footer_html(message, link)
    headers.update(unsubscribe_service.headers_for(link))
    signature = mailbox.signature_html if mailbox else ""
    text = _text(message, signature, link)
    mail = _UnsubscribeMail(subject, text, sender, to, cc=cc, bcc=bcc, headers=headers, connection=connection)
    mail.attach_alternative(_html(message, signature, link), "text/html")
    return mail


def _transport(channel: Channel, mailbox: Mailbox | None) -> tuple[BaseEmailBackend, str]:
    """(SMTP connection, From) of the mailbox, else of the channel's django_email entry."""
    if mailbox is not None:
        return mailbox_service.smtp_connection(mailbox), sender(channel, mailbox)
    connection = EmailDomain(channel_idx=channel.idx).get_channel_smtp_connection_if_exists()
    if connection is None:
        raise SmtpNotConfiguredError(f"no SMTP configuration for channel {channel.idx}")
    return connection, sender(channel, mailbox)


def sender(channel: Channel, mailbox: Mailbox | None) -> str:
    """The From of a mail: the thread's mailbox, else the channel's sender address."""
    return mailbox_service.sender(mailbox) if mailbox is not None else from_email(channel)


def sender_address(thread: Thread) -> str:
    """The bare address every mail of the thread leaves from — no query with `channel` and `mailbox` loaded."""
    return parseaddr(sender(thread.channel, thread.mailbox))[1]


def _copies(addresses: list[str], channel: Channel, headers: dict[str, str], header: str) -> list[str]:
    """The mailbox copy addresses (CC or BCC — BCC is an envelope recipient only, never a header); a sandbox only
    names them in `X-Original-Cc` / `X-Original-Bcc`, nothing leaves for them."""
    if not addresses:
        return []
    if channel.mode == ChannelMode.SANDBOX:
        headers[header] = ", ".join(addresses)
        return []
    return list(addresses)


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
    """`In-Reply-To` / `References` from the Message-IDs of earlier sent messages in the thread (C-28); a message
    answering a reply points `In-Reply-To` at the reply and ends `References` with it."""
    earlier = (
        Message.objects.filter(thread_id=message.thread_id, status=MessageStatus.SENT)
        .exclude(pk=message.pk)
        .exclude(message_id="")
        .order_by("sent_at", "pk")
        .values_list("message_id", flat=True)
    )
    ids = list(earlier)
    if message.in_reply_to_id:  # a hand-written answer threads under the reply it answers
        ids.append(Reply.objects.values_list("inbound_message_id", flat=True).get(pk=message.in_reply_to_id))
    return {"In-Reply-To": ids[-1], "References": " ".join(ids)} if ids else {}


def footer_html(message: Message, link: str = "") -> str:
    """The channel footer for the body language and the mailbox brand (`footer_service.resolve`) around the legal
    text, the unsubscribe link at `{{ unsubscribe }}` or below; empty when no footer matches — the legal text and the
    link then go out alone, as before footers existed."""
    footer = _footer(message)
    if footer is None:
        return ""
    return footer_service.render(footer, message.legal_footer, link, _unsubscribe_label(message, footer))


def _footer(message: Message) -> MailFooter | None:
    mailbox = message.thread.mailbox
    brand = mailbox.brand if mailbox is not None else ""
    return footer_service.resolve(message.thread.channel, _body_language(message), brand)


def _body_language(message: Message) -> str:
    """The language of the template version the body was rendered from — the template cascade may have fallen
    back from the recipient language; the recipient language when the message has no version."""
    if message.template_version_id:
        versions = MessageTemplateVersion.objects.filter(pk=message.template_version_id)
        if language := versions.values_list("template__language__iso2", flat=True).first():
            return language
    thread = message.thread
    return thread.recipient_language.iso2 if thread.recipient_language else ""


def _text(message: Message, signature: str, link: str) -> str:
    """Body, signature, then after `-- ` the footer (the link inside it, as `label <url>`) or the legal text and the
    link as `label: url` — one line, so a quoted reply never reads our link as the recipient's opt-out wording."""
    body = message.body_text
    if signature and (signature_text := footer_service.to_text(signature)):
        body += "\n\n" + signature_text
    if message.footer_html:
        footer = footer_service.to_text(message.footer_html)
    else:
        footer = message.legal_footer.strip()
        if link:
            footer = (footer + "\n\n" if footer else "") + f"{_unsubscribe_label(message, None)}: {link}"
    return body + (FOOTER_SEPARATOR + footer if footer else "")


def _html(message: Message, signature: str, link: str) -> str:
    body = (message.body_html or footer_service.paragraphs(message.body_text)) + signature
    if message.footer_html:
        return body + message.footer_html  # the unsubscribe link is part of the rendered footer
    footer = message.legal_footer.strip()
    unsubscribe = footer_service.unsubscribe_paragraph(link, _unsubscribe_label(message, None)) if link else ""
    return body + (f"<p>{escape(footer)}</p>" if footer else "") + unsubscribe


def _unsubscribe_label(message: Message, footer: MailFooter | None) -> str:
    return footer_service.unsubscribe_label(footer, _body_language(message))
