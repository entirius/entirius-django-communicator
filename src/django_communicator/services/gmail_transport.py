# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Sending through the Gmail API as an alternative to SMTP, chosen per sender route (`SenderRoute.transport`).

The RFC 822 message is the one `mail_builder.build` makes for SMTP (headers, footer, unsubscribe, DW in CC); only the
last step differs. The API is called only when the channel is live (the live gate was checked by `delivery_service`)
or, in a sandbox, when `COMMUNICATOR_GMAIL_API_BASE` is not Google's (the test double). A dry run, and a sandbox
facing the real Google, never get here: the mail goes the SMTP way as before.
"""

from email import policy as email_policy

from django.core.mail import EmailMultiAlternatives
from django.core.mail.message import EmailMessage

from django_communicator.enums import ChannelMode, MailAccountState, MailTransport
from django_communicator.models import Channel, MailAccount, Message, SenderRoute
from django_communicator.services import gmail_client, mail_account_service, sender_route_service


def uses_api(channel: Channel) -> bool:
    """Whether a gmail_api route sends through the API in this channel mode."""
    if channel.mode == ChannelMode.LIVE:
        return True
    return channel.mode == ChannelMode.SANDBOX and not gmail_client.is_real_google()


def account_for(message: Message) -> MailAccount | None:
    """The account the message leaves through; None = SMTP (route without gmail_api, dry run, sandbox vs real Google).
    Raises `SenderRouteError` for an unusable route (the caller has checked that already)."""
    channel = message.thread.channel
    route = sender_route_service.route_for(message)
    if route is None or route.transport != MailTransport.GMAIL_API or not uses_api(channel):
        return None
    return route.mail_account


def route_blocked(route: SenderRoute | None, channel: Channel) -> bool:
    """A gmail_api route whose account cannot send (missing, not connected, needs re-authorisation): its mail waits,
    like on a paused route. Never blocks a route that would go out by SMTP anyway."""
    if route is None or route.transport != MailTransport.GMAIL_API or not uses_api(channel):
        return False
    account = route.mail_account
    return account is None or account.state != MailAccountState.CONNECTED


def send(account: MailAccount, mail: EmailMultiAlternatives) -> str:
    """`users.messages.send` in the name of the account; the Gmail message id. Raises `gmail_client.GmailError`."""
    raw = _raw(mail)
    return mail_account_service.with_token(account, lambda token: gmail_client.send(token, raw))


def _raw(mail: EmailMessage) -> bytes:
    """The bytes the SMTP backend would put on the wire (same policy), so both transports send the same message."""
    return mail.message(policy=email_policy.SMTP).as_bytes()
