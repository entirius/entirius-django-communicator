# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""One-click unsubscribe (outbound Q6, RFC 8058): a signed token per (channel, address), the headers of the mail
and the action behind the public endpoint.

The token is `django.core.signing` over `{c: channel idx, e: address}` with a salt of its own; it never expires (a
link in an old mail must keep working) and carries nothing secret. Unsubscribing is idempotent: the address is
suppressed on the channel, its running sequences stop and `optout_confirmed` tells the owner of each `subject_ref`.
"""

import logging

from django.core import signing
from django.db import transaction

from django_communicator import settings as communicator_settings
from django_communicator.enums import SequenceStopReason
from django_communicator.models import Channel, Reply, Thread
from django_communicator.services import optout_service, sequence_service, suppression_service
from django_communicator.services.exceptions import ClientError
from django_communicator.signals import optout_confirmed

logger = logging.getLogger(__name__)

SALT = "django_communicator.unsubscribe"
PATH = "/api/communicator/v2/public/unsubscribe/{token}/"
REASON = "unsubscribe link"


class InvalidTokenError(ClientError):
    """The token is not one this module signed."""


def make_token(channel_idx: str, email: str) -> str:
    return signing.dumps({"c": channel_idx, "e": email.strip().lower()}, salt=SALT, compress=True)


def read_token(token: str) -> tuple[str, str]:
    """(channel idx, address). Raises `InvalidTokenError`."""
    try:
        data = signing.loads(token, salt=SALT)
        return str(data["c"]), str(data["e"])
    except (signing.BadSignature, KeyError, TypeError):
        raise InvalidTokenError("invalid unsubscribe link") from None


def enabled() -> bool:
    return bool(communicator_settings.COMMUNICATOR_UNSUBSCRIBE_BASE_URL)


def url_for(channel_idx: str, email: str) -> str:
    """The public link of the address; empty while `COMMUNICATOR_UNSUBSCRIBE_BASE_URL` is unset."""
    base = communicator_settings.COMMUNICATOR_UNSUBSCRIBE_BASE_URL.rstrip("/")
    return base + PATH.format(token=make_token(channel_idx, email)) if base else ""


def headers_for(channel_idx: str, email: str) -> dict[str, str]:
    """`List-Unsubscribe` and `List-Unsubscribe-Post` (RFC 8058); none while the feature is off."""
    if not (url := url_for(channel_idx, email)):
        return {}
    return {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def unsubscribe(token: str) -> str:
    """Suppress the address of the token on its channel; returns the address. Raises `InvalidTokenError`, also for a
    channel that no longer exists."""
    channel_idx, email = read_token(token)
    try:
        channel = Channel.objects.get(idx=channel_idx)
    except Channel.DoesNotExist:
        raise InvalidTokenError("invalid unsubscribe link") from None
    with transaction.atomic():
        suppression_service.suppress_email(channel, email, reason=REASON)
        threads = list(Thread.objects.filter(channel=channel, recipient_email__iexact=email))
        for thread in threads:
            sequence_service.stop_sequence(thread, SequenceStopReason.OPTOUT)
        for ref in sorted({thread.subject_ref for thread in threads}):
            transaction.on_commit(
                lambda ref=ref: optout_confirmed.send(
                    sender=Reply, subject_ref=ref, email=email, channel_idx=channel_idx
                )
            )
        transaction.on_commit(lambda: optout_service.record_objection(channel_idx, email))
    logger.info("communicator unsubscribe in channel %s", channel_idx)
    return email
