# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The one do-not-contact store: exact email or registrable domain (`www.shop.pl` == `shop.pl`, never a substring).

A row with a channel holds in that channel, a row without one (global) in every channel. Writers follow
`COMMUNICATOR_SUPPRESSION_SCOPE` for kinds `email` and `domain`; `email_token` is always global. The reader honours
global rows under both settings, so a row written under `install` keeps protecting after a switch to `channel`.
`can_mail` is the one question every sender asks: suppressed, address invalid, or free to mail."""

import logging
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import SuppressionKind, SuppressionScope, SuppressionSource
from django_communicator.models import Channel, Suppression
from django_communicator.services import address_service
from django_communicator.services.exceptions import ClientError
from django_communicator.utils.domains import NOT_A_DOMAIN, NOT_AN_EMAIL, email_domain, registrable_domain
from django_communicator.utils.emails import anonymised_address, is_anonymised, normalize_email

logger = logging.getLogger(__name__)

SUPPRESSED = "suppressed"
ADDRESS_INVALID = "address_invalid"
SCOPED_KINDS = (SuppressionKind.EMAIL, SuppressionKind.DOMAIN)


class DuplicateSuppressionError(ClientError):
    pass


class InvalidSuppressionValueError(ClientError, ValueError):
    """A value that can never match an address (still a ``ValueError`` for callers that catch it so)."""


def scope() -> str:
    """`channel` or `install` — an install fact, never inferred from rows."""
    value = communicator_settings.COMMUNICATOR_SUPPRESSION_SCOPE
    if value not in SuppressionScope.values:
        raise ImproperlyConfigured(f"COMMUNICATOR_SUPPRESSION_SCOPE must be one of {SuppressionScope.values}")
    return value


def row_scope(row: Suppression) -> str:
    return SuppressionScope.INSTALL if row.channel_id is None else SuppressionScope.CHANNEL


def _owner(channel: Channel, asked: str | None = None) -> Channel | None:
    """The channel a new `email` / `domain` row belongs to: None (global) for `install` — the scope asked for, else
    the configured one."""
    return None if (asked or scope()) == SuppressionScope.INSTALL else channel


def _keys(email: str) -> tuple[str, str, str]:
    """(token, address, domain) a suppression row of a normalised address may hold."""
    try:
        domain = email_domain(email)
    except ValueError:
        domain = ""
    return anonymised_address(email), email, domain


def _matching(channel: Channel, email: str) -> Q:
    """The token row of the address, and email / domain rows of the channel or global."""
    token, email, domain = _keys(email)
    erased = Q(channel=None, kind=SuppressionKind.EMAIL_TOKEN, value=token)
    listed = Q(kind=SuppressionKind.EMAIL, value=email) | Q(kind=SuppressionKind.DOMAIN, value=domain)
    return erased | (listed & (Q(channel=channel) | Q(channel=None)))


def is_suppressed(channel: Channel, email: str) -> bool:
    """Listed for the channel or globally, or its token suppressed (erased or anonymised); a token address itself
    is suppressed without a row. One query."""
    email = normalize_email(email)
    return is_anonymised(email) or Suppression.objects.filter(_matching(channel, email)).exists()


def can_mail(channel: Channel, email: str) -> str | None:
    """`address_invalid`, `suppressed` or None (free to mail). At most two queries."""
    if address_service.is_invalid(email):
        return ADDRESS_INVALID
    return SUPPRESSED if is_suppressed(channel, email) else None


def can_mail_many(channel: Channel, emails: list[str]) -> dict[str, str | None]:
    """`can_mail` of every address, keyed by the address as given — two queries for any number of addresses."""
    keys = {email: _keys(normalize_email(email)) for email in emails}
    invalid = address_service.invalid_among(emails)
    listed = _listed(channel, list(keys.values()))

    def refusal(token: str, address: str, domain: str) -> str | None:
        if address in invalid:
            return ADDRESS_INVALID
        rows = {
            (SuppressionKind.EMAIL_TOKEN, token),
            (SuppressionKind.EMAIL, address),
            (SuppressionKind.DOMAIN, domain),
        }
        return SUPPRESSED if is_anonymised(address) or rows & listed else None

    return {email: refusal(*key) for email, key in keys.items()}


def _listed(channel: Channel, keys: list[tuple[str, str, str]]) -> set[tuple[str, str]]:
    """(kind, value) of every suppression row matching one of the addresses' keys."""
    tokens, addresses, domains = ({key[index] for key in keys} for index in range(3))
    erased = Q(channel=None, kind=SuppressionKind.EMAIL_TOKEN, value__in=tokens)
    listed = Q(kind=SuppressionKind.EMAIL, value__in=addresses) | Q(kind=SuppressionKind.DOMAIN, value__in=domains)
    rows = Suppression.objects.filter(erased | (listed & (Q(channel=channel) | Q(channel=None))))
    return set(rows.values_list("kind", "value"))


def explain(channel: Channel, email: str) -> dict[str, Any]:
    """Why (not): the `can_mail` answer, the matching suppression rows and the address status."""
    email = normalize_email(email)
    rows = Suppression.objects.filter(_matching(channel, email)).order_by("created_at", "pk")
    status = address_service.status_of(email)
    return {
        "email": email,
        "refusal": can_mail(channel, email),
        "suppressions": [_explained(row) for row in rows],
        "address_status": status,
    }


def _explained(row: Suppression) -> dict[str, Any]:
    fields = {"id": row.pk, "kind": row.kind, "source": row.source, "reason": row.reason}
    return {**fields, "scope": row_scope(row), "created_at": row.created_at}


def suppress_token(token: str, *, reason: str) -> None:
    """Idempotent global suppression of an erased or anonymised address by its token (never the plain address)."""
    row = Suppression(
        channel=None, kind=SuppressionKind.EMAIL_TOKEN, value=token, source=SuppressionSource.ERASED, reason=reason
    )
    Suppression.objects.bulk_create([row], ignore_conflicts=True)


def normalise_value(kind: str, value: str) -> str:
    """Lower-cased email, or the registrable domain of a domain; raises `InvalidSuppressionValueError` (a
    `ValueError`) on a value that cannot match."""
    value = value.strip().lower()
    if kind == SuppressionKind.DOMAIN:
        if "@" in value:
            raise InvalidSuppressionValueError("a domain has no @")
        if "\x00" in value:  # PostgreSQL text cannot store NUL
            raise InvalidSuppressionValueError(NOT_A_DOMAIN)
        try:
            return registrable_domain(value)
        except ValueError:
            raise InvalidSuppressionValueError(NOT_A_DOMAIN) from None
    local, _, host = value.partition("@")
    if not local or "@" in host or "\x00" in value or not _is_dotted(host):
        raise InvalidSuppressionValueError(NOT_AN_EMAIL)
    return value


def _is_dotted(host: str) -> bool:
    labels = host.split(".")
    return len(labels) > 1 and all(labels)


def list_suppressions(channel: Channel, filters: dict[str, str] | None = None) -> QuerySet[Suppression]:
    """The channel's rows and the global rows. Filters: `value` (one normalised value), `scope` (`channel` /
    `install`), `source`, `search` (part of the value)."""
    filters = {key: value.strip() for key, value in (filters or {}).items() if value and value.strip()}
    rows = Suppression.objects.filter(Q(channel=channel) | Q(channel=None)).order_by("kind", "value", "pk")
    if "scope" in filters:
        rows = rows.filter(channel__isnull=filters["scope"] == SuppressionScope.INSTALL)
    if "source" in filters:
        rows = rows.filter(source=filters["source"])
    if "search" in filters:
        rows = rows.filter(value__icontains=filters["search"])
    return rows.filter(value=filters["value"].lower()) if "value" in filters else rows


def create_suppression(
    channel: Channel,
    *,
    kind: str,
    value: str,
    reason: str = "",
    source: str = SuppressionSource.MANUAL,
    user=None,
    scope: str | None = None,
) -> Suppression:
    """A channel row, or a global one for `scope="install"` (None = the configured scope). Raises `InvalidSuppressionValueError` (a
    `ValueError`) on an unusable value or kind `email_token` (written by erasure only), `DuplicateSuppressionError`
    when it is already listed in that scope."""
    if kind not in SCOPED_KINDS:
        raise InvalidSuppressionValueError("only email and domain suppressions can be added")
    normalised = normalise_value(kind, value)
    owner = _owner(channel, scope)
    try:
        with transaction.atomic():
            return Suppression.objects.create(
                channel=owner, kind=kind, value=normalised, source=source, reason=reason, created_by=user
            )
    except IntegrityError:
        raise DuplicateSuppressionError(f"{kind} {normalised} is already suppressed") from None


def suppress_email(channel: Channel, email: str, *, source: str, reason: str = "", user=None) -> None:
    """Idempotent: an address already listed keeps its row; an unusable address is only logged."""
    try:
        create_suppression(channel, kind=SuppressionKind.EMAIL, value=email, reason=reason, source=source, user=user)
    except DuplicateSuppressionError:
        return
    except ValueError:
        logger.warning("communicator cannot suppress an unusable address in channel %s", channel.idx)


def suppress_email_for(channel: Channel, email: str, *, source: str, reason: str = "") -> bool:
    """The `email` row of the address in the configured scope carries `source`: created, or an existing row of
    another source takes it. False when the row already had this source or the address is unusable."""
    try:
        value = normalise_value(SuppressionKind.EMAIL, email)
    except ValueError:
        logger.warning("communicator cannot suppress an unusable address in channel %s", channel.idx)
        return False
    row, created = Suppression.objects.get_or_create(
        channel=_owner(channel), kind=SuppressionKind.EMAIL, value=value, defaults={"source": source, "reason": reason}
    )
    if created or row.source == source:
        return created
    Suppression.objects.filter(pk=row.pk).update(source=source, reason=reason, modified_at=timezone.now())
    return True


def delete_suppression(channel: Channel, pk: int) -> None:
    """A row of this channel or a global email / domain row; raises `Suppression.DoesNotExist` otherwise (a token
    row of an erased address is never removed by hand)."""
    rows = Suppression.objects.filter(Q(channel=channel) | Q(channel=None, kind__in=SCOPED_KINDS))
    rows.get(pk=pk).delete()


def copy_to_install(*, apply: bool) -> int:
    """Global rows for every channel `email` / `domain` row not yet global; the number of global rows (to be)
    created. Idempotent; the oldest channel row gives source and reason. Nothing is written unless `apply`."""
    globals_ = Suppression.objects.filter(channel=None, kind__in=SCOPED_KINDS).values_list("kind", "value")
    existing = set(globals_)
    rows = Suppression.objects.filter(channel__isnull=False, kind__in=SCOPED_KINDS).order_by("created_at", "pk")
    copies: dict[tuple[str, str], Suppression] = {}
    for row in rows.iterator():
        key = (row.kind, row.value)
        if key not in existing and key not in copies:
            copies[key] = Suppression(
                channel=None, kind=row.kind, value=row.value, source=row.source, reason=row.reason
            )
    if apply:
        Suppression.objects.bulk_create(copies.values(), ignore_conflicts=True)
    return len(copies)
