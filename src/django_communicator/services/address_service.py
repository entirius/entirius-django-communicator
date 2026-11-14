# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Address status, install-wide: a hard bounce or a failed check writes it, only `invalid` blocks mail.

Verification is syntax → disposable domain → DNS (domain exists, MX, null MX, A/AAAA fallback = `risky`); never a
mailbox probe and never an external API. The DNS resolver is injected (`resolver=` or `use_resolver`): the module
suite installs a stub and never touches the network; the real one needs the optional extra `verify` (dnspython).
A hard bounce is final: `invalid / hard_bounce` is never re-checked. Runs outside transactions and locks."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q, QuerySet
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import AddressReason, AddressState
from django_communicator.models import AddressStatus
from django_communicator.utils.emails import is_anonymised, normalize_email

DNS_TIMEOUT_S = 5
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


@dataclass(frozen=True)
class DnsAnswer:
    """What the resolver found for a domain; `error` = the lookup itself failed (timeout, no nameserver)."""

    exists: bool = True
    mx_hosts: tuple[str, ...] = ()
    null_mx: bool = False
    has_address: bool = False
    error: bool = False


class Resolver(Protocol):
    def lookup(self, domain: str) -> DnsAnswer: ...


class DnsPythonResolver:
    """The real resolver (dnspython, extra `verify`). Never used by the module suite."""

    def lookup(self, domain: str) -> DnsAnswer:
        try:
            import dns.exception
            import dns.resolver
        except ImportError:
            return DnsAnswer(error=True)
        resolver = dns.resolver.Resolver()
        resolver.lifetime = DNS_TIMEOUT_S
        try:
            answers = resolver.resolve(domain, "MX")
        except dns.resolver.NXDOMAIN:
            return DnsAnswer(exists=False)
        except dns.resolver.NoAnswer:
            return _address_fallback(resolver, domain)
        except (dns.exception.DNSException, OSError):
            return DnsAnswer(error=True)
        hosts = [str(record.exchange).rstrip(".") for record in sorted(answers, key=lambda r: r.preference)]
        if hosts == [""]:
            return DnsAnswer(null_mx=True)
        return DnsAnswer(mx_hosts=tuple(host for host in hosts if host))


def _address_fallback(resolver, domain: str) -> DnsAnswer:
    import dns.exception
    import dns.resolver

    for record in ("A", "AAAA"):
        try:
            resolver.resolve(domain, record)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            continue
        except (dns.exception.DNSException, OSError):
            return DnsAnswer(error=True)
        return DnsAnswer(has_address=True)
    return DnsAnswer()


_resolver: Resolver | None = None


def use_resolver(resolver: Resolver | None) -> None:
    """Install the resolver every check uses (None = the real one)."""
    global _resolver
    _resolver = resolver


def status_of(email: str) -> AddressStatus | None:
    return AddressStatus.objects.filter(email=normalize_email(email)).first()


def is_invalid(email: str) -> bool:
    return AddressStatus.objects.filter(email=normalize_email(email), status=AddressState.INVALID).exists()


def invalid_among(emails: list[str]) -> set[str]:
    """The normalised addresses of `emails` whose status is invalid — one query."""
    normalised = {normalize_email(email) for email in emails}
    rows = AddressStatus.objects.filter(email__in=normalised, status=AddressState.INVALID)
    return set(rows.values_list("email", flat=True))


def check(email: str, *, resolver: Resolver | None = None) -> tuple[str, str]:
    """(status, reason) of one address; pure apart from the resolver."""
    email = normalize_email(email)
    if _bad_syntax(email):
        return AddressState.INVALID, AddressReason.SYNTAX
    domain = email.rsplit("@", 1)[1]
    if _is_disposable(domain):
        return AddressState.INVALID, AddressReason.DISPOSABLE
    return dns_verdict((resolver or _resolver or DnsPythonResolver()).lookup(domain))


def _bad_syntax(email: str) -> bool:
    if len(email) > 254 or email.count("@") != 1:
        return True
    local, domain = email.split("@")
    if len(local) > 64 or not _DOMAIN_RE.match(domain):
        return True
    try:
        validate_email(email)
    except ValidationError:
        return True
    return False


def _is_disposable(domain: str) -> bool:
    listed = {item.lower() for item in communicator_settings.COMMUNICATOR_DISPOSABLE_DOMAINS}
    parts = domain.split(".")
    return any(".".join(parts[index:]) in listed for index in range(len(parts) - 1))


def dns_verdict(answer: DnsAnswer) -> tuple[str, str]:
    if answer.error:
        return AddressState.UNKNOWN, AddressReason.DNS_ERROR
    if not answer.exists:
        return AddressState.INVALID, AddressReason.DOMAIN_NOT_FOUND
    if answer.null_mx:
        return AddressState.INVALID, AddressReason.NULL_MX
    if answer.mx_hosts:
        return AddressState.VALID, AddressReason.MX_OK
    if answer.has_address:
        return AddressState.RISKY, AddressReason.NO_MX_ADDRESS_FALLBACK
    return AddressState.INVALID, AddressReason.NO_MAIL_SERVER


def verify(email: str, *, resolver: Resolver | None = None) -> AddressStatus:
    """Check the address and store the result; a hard bounce stays as it is."""
    email = normalize_email(email)
    current = status_of(email)
    if current is not None and current.reason == AddressReason.HARD_BOUNCE:
        return current
    status, reason = check(email, resolver=resolver)
    return _store(email, status, reason)


def mark_hard_bounce(email: str) -> AddressStatus:
    """`invalid / hard_bounce`, final. Live bounces only: a sandbox bounce says nothing about the address."""
    return _store(normalize_email(email), AddressState.INVALID, AddressReason.HARD_BOUNCE)


def _store(email: str, status: str, reason: str) -> AddressStatus:
    fields = {"status": status, "reason": reason, "verified_at": timezone.now()}
    row, _ = AddressStatus.objects.update_or_create(email=email, defaults=fields)
    return row


def _cutoff(now: datetime) -> datetime:
    return now - timedelta(days=communicator_settings.COMMUNICATOR_VERIFY_MAX_AGE_DAYS)


def is_due(row: AddressStatus | None, now: datetime) -> bool:
    """Never checked, or checked before the maximum age; a hard bounce never."""
    if row is None:
        return True
    if row.reason == AddressReason.HARD_BOUNCE:
        return False
    return row.verified_at is None or row.verified_at < _cutoff(now)


def verify_if_due(email: str, now: datetime) -> None:
    """`communicate()` with `COMMUNICATOR_VERIFY_ENABLED`: an unknown or stale address is checked before drafting (a
    token address never: it is suppressed anyway)."""
    if not is_anonymised(email) and is_due(status_of(email), now):
        verify(email)


def stale(now: datetime) -> QuerySet[AddressStatus]:
    """Rows to re-check, oldest first: past the maximum age, never checked, or a DNS error; never a hard bounce."""
    due = Q(verified_at__isnull=True) | Q(verified_at__lt=_cutoff(now)) | Q(reason=AddressReason.DNS_ERROR)
    rows = AddressStatus.objects.filter(due).exclude(reason=AddressReason.HARD_BOUNCE)
    return rows.order_by("verified_at", "pk")


def reverify_stale(limit: int) -> int:
    """Re-check up to `limit` stale rows; the number checked."""
    emails = list(stale(timezone.now()).values_list("email", flat=True)[:limit])
    for email in emails:
        verify(email)
    return len(emails)


def forget(emails: list[str]) -> int:
    """GDPR erasure and anonymisation: the status rows of the addresses go."""
    deleted, _ = AddressStatus.objects.filter(email__in=[normalize_email(email) for email in emails]).delete()
    return deleted
