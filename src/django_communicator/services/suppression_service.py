# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Suppression list: exact email or registrable domain (`www.shop.pl` == `shop.pl`, never a substring)."""

import logging

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet

from django_communicator.enums import SuppressionKind
from django_communicator.models import Channel, Suppression
from django_communicator.utils.domains import email_domain, registrable_domain
from django_communicator.utils.emails import anonymised_address, is_anonymised

logger = logging.getLogger(__name__)


class DuplicateSuppressionError(Exception):
    pass


def is_suppressed(channel: Channel, email: str) -> bool:
    """The address's token suppressed globally (erased or anonymised), or its email or domain listed on the channel;
    a token address itself is suppressed without a row."""
    email = email.strip().lower()
    if is_anonymised(email):
        return True
    try:
        domain = email_domain(email)
    except ValueError:
        domain = ""
    erased = Q(channel=None, kind=SuppressionKind.EMAIL_TOKEN, value=anonymised_address(email))
    listed = Q(kind=SuppressionKind.EMAIL, value=email) | Q(kind=SuppressionKind.DOMAIN, value=domain)
    return Suppression.objects.filter(erased | Q(listed, channel=channel)).exists()


def suppress_token(token: str, *, reason: str) -> None:
    """Idempotent global suppression of an erased or anonymised address by its token (never the plain address)."""
    row = Suppression(channel=None, kind=SuppressionKind.EMAIL_TOKEN, value=token, reason=reason)
    Suppression.objects.bulk_create([row], ignore_conflicts=True)


def normalise_value(kind: str, value: str) -> str:
    """Lower-cased email, or the registrable domain of a domain; raises `ValueError` on a value that cannot match."""
    value = value.strip().lower()
    if kind == SuppressionKind.DOMAIN:
        if "@" in value:
            raise ValueError("a domain has no @")
        return registrable_domain(value)
    local, _, host = value.partition("@")
    if not local or "@" in host or not _is_dotted(host):
        raise ValueError("not an email address")
    return value


def _is_dotted(host: str) -> bool:
    labels = host.split(".")
    return len(labels) > 1 and all(labels)


def list_suppressions(channel: Channel, value: str = "") -> QuerySet[Suppression]:
    """The channel's rows and the global token rows; `value` narrows to one normalised value."""
    rows = Suppression.objects.filter(Q(channel=channel) | Q(channel=None)).order_by("kind", "value")
    return rows.filter(value=value.strip().lower()) if value.strip() else rows


def create_suppression(channel: Channel, *, kind: str, value: str, reason: str = "", user=None) -> Suppression:
    """Raises `ValueError` on an unusable value and `DuplicateSuppressionError` when it is already listed."""
    normalised = normalise_value(kind, value)
    try:
        with transaction.atomic():
            return Suppression.objects.create(
                channel=channel, kind=kind, value=normalised, reason=reason, created_by=user
            )
    except IntegrityError:
        raise DuplicateSuppressionError(f"{kind} {normalised} is already suppressed") from None


def suppress_email(channel: Channel, email: str, *, reason: str, user=None) -> None:
    """Idempotent: an address already listed keeps its row; an unusable address is only logged."""
    try:
        create_suppression(channel, kind=SuppressionKind.EMAIL, value=email, reason=reason, user=user)
    except DuplicateSuppressionError:
        return
    except ValueError:
        logger.warning("communicator cannot suppress an unusable address in channel %s", channel.idx)


def delete_suppression(channel: Channel, pk: int) -> None:
    """Raises `Suppression.DoesNotExist` when the row is not in this channel."""
    Suppression.objects.get(channel=channel, pk=pk).delete()
