# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Suppression list: exact email or registrable domain (`www.shop.pl` == `shop.pl`, never a substring)."""

from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet

from django_communicator.enums import SuppressionKind
from django_communicator.models import Channel, Suppression
from django_communicator.utils.domains import email_domain, registrable_domain


class DuplicateSuppressionError(Exception):
    pass


def is_suppressed(channel: Channel, email: str) -> bool:
    email = email.strip().lower()
    try:
        domain = email_domain(email)
    except ValueError:
        domain = ""
    matches = Q(kind=SuppressionKind.EMAIL, value=email) | Q(kind=SuppressionKind.DOMAIN, value=domain)
    return Suppression.objects.filter(matches, channel=channel).exists()


def normalise_value(kind: str, value: str) -> str:
    """Lower-cased email, or the registrable domain of a domain; raises `ValueError` on unusable input."""
    value = value.strip().lower()
    if kind == SuppressionKind.DOMAIN:
        return registrable_domain(value)
    if "@" not in value:
        raise ValueError("not an email address")
    return value


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


def delete_suppression(channel: Channel, pk: int) -> None:
    """Raises `Suppression.DoesNotExist` when the row is not in this channel."""
    Suppression.objects.get(channel=channel, pk=pk).delete()
