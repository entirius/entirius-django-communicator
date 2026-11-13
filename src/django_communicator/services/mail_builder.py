# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Outgoing mail of a message: multipart/alternative, our Message-ID, threading headers, channel mode addressing.

The SMTP connection is the channel's one from django_email; this module never reads SMTP credentials itself.
"""

import secrets
from email.headerregistry import BaseHeader, HeaderRegistry
from email.policy import EmailPolicy
from email.utils import parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape
from django_email.domain import EmailDomain

from django_communicator import settings as communicator_settings
from django_communicator.enums import ChannelMode, MessageStatus
from django_communicator.models import Channel, Message, MessageTemplateVersion
from django_communicator.services import footer_service, unsubscribe_service

FOOTER_SEPARATOR = "\n\n-- \n"
UNSUBSCRIBE_LABELS = {"pl": "Wypisz się z listy"}
UNSUBSCRIBE_LABEL = "Unsubscribe"


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
    `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` set the mail carries the RFC 8058 headers and a link in both parts."""
    channel = message.thread.channel
    connection = EmailDomain(channel_idx=channel.idx).get_channel_smtp_connection_if_exists()
    if connection is None:
        raise SmtpNotConfiguredError(f"no SMTP configuration for channel {channel.idx}")
    sender = from_email(channel)
    to, subject, headers = _addressing(message, channel)
    headers.update(_threading_headers(message))
    headers["Message-ID"] = message.message_id or (  # a soft-bounce retry keeps the Message-ID it was sent with
        f"<communicator-{message.pk}-{secrets.token_hex(4)}@{parseaddr(sender)[1].rpartition('@')[2]}>"
    )
    message.footer_html = message.footer_html or footer_html(message)
    link = unsubscribe_service.url_for(channel.idx, message.thread.recipient_email)
    headers.update(unsubscribe_service.headers_for(link))
    mail = _UnsubscribeMail(subject, _text(message, link), sender, to, headers=headers, connection=connection)
    mail.attach_alternative(_html(message, link), "text/html")
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


def footer_html(message: Message) -> str:
    """The channel footer for the body language (cascade to the channel default) around the legal text; empty
    when neither has a footer — the legal text then goes out alone, as before footers existed."""
    footer = footer_service.resolve(message.thread.channel, _body_language(message))
    return footer_service.render(footer, message.legal_footer) if footer else ""


def _body_language(message: Message) -> str:
    """The language of the template version the body was rendered from — the template cascade may have fallen
    back from the recipient language; the recipient language when the message has no version."""
    if message.template_version_id:
        versions = MessageTemplateVersion.objects.filter(pk=message.template_version_id)
        if language := versions.values_list("template__language__iso2", flat=True).first():
            return language
    thread = message.thread
    return thread.recipient_language.iso2 if thread.recipient_language else ""


def _text(message: Message, link: str) -> str:
    if message.footer_html:
        footer = footer_service.to_text(message.footer_html)
    else:
        footer = message.legal_footer.strip()
    if link:
        footer = (footer + "\n\n" if footer else "") + f"{_unsubscribe_label(message)}: {link}"
    return message.body_text + (FOOTER_SEPARATOR + footer if footer else "")


def _html(message: Message, link: str) -> str:
    body = message.body_html or footer_service.paragraphs(message.body_text)
    unsubscribe = f'<p><a href="{escape(link)}">{escape(_unsubscribe_label(message))}</a></p>' if link else ""
    if message.footer_html:
        return body + message.footer_html + unsubscribe
    footer = message.legal_footer.strip()
    return body + (f"<p>{escape(footer)}</p>" if footer else "") + unsubscribe


def _unsubscribe_label(message: Message) -> str:
    return UNSUBSCRIBE_LABELS.get(_body_language(message).lower(), UNSUBSCRIBE_LABEL)
