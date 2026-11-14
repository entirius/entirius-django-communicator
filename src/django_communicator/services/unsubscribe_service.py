# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""One-click unsubscribe (RFC 8058): a signed token per (channel, address), the link and headers of a mail and the
action behind the public endpoint.

The token is `django.core.signing` over `{c: channel idx, e: address}` with a salt of its own; it never expires (a
link in an old mail must keep working) and carries nothing secret. Unsubscribing is idempotent: the address is
suppressed on the channel, its sequences stop, `optout_confirmed` tells the owner of each `subject_ref` and the
agreements objection is recorded.
"""

import logging

from django.core import signing
from django.db import transaction

from django_communicator import settings as communicator_settings
from django_communicator.enums import SequenceStopReason, SuppressionSource
from django_communicator.models import Channel, Reply, Thread
from django_communicator.services import optout_service, sequence_service, suppression_service
from django_communicator.services.exceptions import ClientError
from django_communicator.signals import optout_confirmed

logger = logging.getLogger(__name__)

SALT = "django_communicator.unsubscribe"
PATH_PREFIX = "/api/communicator/v2/public/unsubscribe/"
PATH = PATH_PREFIX + "{token}/"
REASON = "unsubscribe link"
INVALID = "invalid unsubscribe link"


class InvalidTokenError(ClientError):
    """The token is not one this module signed, or its channel no longer exists."""


def make_token(channel_idx: str, email: str) -> str:
    return signing.dumps({"c": channel_idx, "e": email.strip().lower()}, salt=SALT, compress=True)


def read_token(token: str) -> tuple[Channel, str]:
    """(channel, address). Raises `InvalidTokenError`."""
    try:
        data = signing.loads(token, salt=SALT)
        channel_idx, email = str(data["c"]), str(data["e"])
    except (signing.BadSignature, KeyError, TypeError):
        raise InvalidTokenError(INVALID) from None
    channel = Channel.objects.select_related("default_language").filter(idx=channel_idx).first()
    if channel is None:
        raise InvalidTokenError(INVALID)
    return channel, email


def url_for(channel_idx: str, email: str) -> str:
    """The public link of the address; empty while `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` is unset."""
    base = communicator_settings.COMMUNICATOR_UNSUBSCRIBE_BASE_URL.rstrip("/")
    return base + PATH.format(token=make_token(channel_idx, email)) if base else ""


def headers_for(url: str) -> dict[str, str]:
    """`List-Unsubscribe` and `List-Unsubscribe-Post` (RFC 8058); none for an empty URL."""
    if not url:
        return {}
    return {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


@transaction.atomic
def unsubscribe(token: str) -> tuple[Channel, str]:
    """Suppress the address of the token on its channel; returns the channel and the address. An address already
    suppressed for another reason (bounce, manual) takes the unsubscribe reason and the objection is recorded; one
    already unsubscribed changes nothing more (a second click is harmless). Raises `InvalidTokenError`."""
    channel, email = read_token(token)
    if not suppression_service.suppress_email_for(
        channel, email, source=SuppressionSource.UNSUBSCRIBE_LINK, reason=REASON
    ):
        return channel, email
    threads = list(Thread.objects.filter(channel=channel, recipient_email__iexact=email))
    for thread in threads:
        sequence_service.stop_sequence(thread, SequenceStopReason.OPTOUT)
    for ref in sorted({thread.subject_ref for thread in threads}):
        transaction.on_commit(lambda ref=ref: _confirmed(ref, email, channel.idx))
    transaction.on_commit(lambda: optout_service.record_objection(channel.idx, email))
    logger.info("communicator unsubscribe in channel %s", channel.idx)
    return channel, email


def _confirmed(subject_ref: str, email: str, channel_idx: str) -> None:
    optout_confirmed.send(sender=Reply, subject_ref=subject_ref, email=email, channel_idx=channel_idx)
