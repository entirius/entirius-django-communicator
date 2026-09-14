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
from django_communicator.utils.emails import is_anonymised

logger = logging.getLogger(__name__)


class DuplicateSuppressionError(Exception):
    pass


def is_suppressed(channel: Channel, email: str) -> bool:
    """Listed email or domain; an anonymised token (retention, GDPR erasure) is suppressed without a row."""
    email = email.strip().lower()
    if is_anonymised(email):
        return True
    try:
        domain = email_domain(email)
    except ValueError:
        domain = ""
    matches = Q(kind=SuppressionKind.EMAIL, value=email) | Q(kind=SuppressionKind.DOMAIN, value=domain)
    return Suppression.objects.filter(matches, channel=channel).exists()


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


def list_suppressions(channel: Channel) -> QuerySet[Suppression]:
    return Suppression.objects.filter(channel=channel).order_by("kind", "value")


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
