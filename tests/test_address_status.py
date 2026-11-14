# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Address status: verification verdicts (stub resolver, no network), hard bounces, re-checks, `can_mail`, the
explain endpoint and GDPR."""

import smtplib
from datetime import timedelta
from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone

from django_communicator import gdpr
from django_communicator import settings as communicator_settings
from django_communicator.enums import AddressReason, AddressState, ChannelMode, MessageStatus, SuppressionSource
from django_communicator.models import AddressStatus
from django_communicator.services import address_service, channel_service, delivery_service, suppression_service
from django_communicator.services.address_service import DnsAnswer
from django_communicator.signals.leads_receivers import on_contact_anonymised
from django_communicator.tasks.reverify_addresses import reverify_addresses
from django_communicator.utils.emails import anonymised_address, email_hash
from tests.conftest import MONDAY_10, api_url, approved_message
from tests.test_inbound import eml, go_live, ingest, make_sent

EMAIL = "jan@example-shop-1.test"
SEND = "django.core.mail.EmailMultiAlternatives.send"


class StubResolver:
    def __init__(self, answer: DnsAnswer) -> None:
        self.answer, self.domains = answer, []

    def lookup(self, domain: str) -> DnsAnswer:
        self.domains.append(domain)
        return self.answer


@pytest.fixture
def resolver():
    stub = StubResolver(DnsAnswer(mx_hosts=("mx.example-shop-1.test",)))
    address_service.use_resolver(stub)
    yield stub
    address_service.use_resolver(None)


@pytest.fixture
def verify_on(monkeypatch, resolver):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_VERIFY_ENABLED", True)
    return resolver


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (DnsAnswer(mx_hosts=("mx.shop.test",)), (AddressState.VALID, AddressReason.MX_OK)),
        (DnsAnswer(exists=False), (AddressState.INVALID, AddressReason.DOMAIN_NOT_FOUND)),
        (DnsAnswer(null_mx=True), (AddressState.INVALID, AddressReason.NULL_MX)),
        (DnsAnswer(has_address=True), (AddressState.RISKY, AddressReason.NO_MX_ADDRESS_FALLBACK)),
        (DnsAnswer(), (AddressState.INVALID, AddressReason.NO_MAIL_SERVER)),
        (DnsAnswer(error=True), (AddressState.UNKNOWN, AddressReason.DNS_ERROR)),
    ],
)
def test_every_dns_verdict(answer, expected):
    stub = StubResolver(answer)
    assert address_service.check("Jan@Sub.Shop.test", resolver=stub) == expected
    assert stub.domains == ["sub.shop.test"]


@pytest.mark.parametrize("email", ["no-at-sign", "a@b@shop.test", "jan@shop", "jan..x@shop.test", "jan@-shop.test"])
def test_bad_syntax_is_invalid_without_a_lookup(email):
    stub = StubResolver(DnsAnswer(mx_hosts=("mx",)))
    assert address_service.check(email, resolver=stub) == (AddressState.INVALID, AddressReason.SYNTAX)
    assert stub.domains == []


def test_disposable_domain_and_its_subdomains_are_invalid():
    stub = StubResolver(DnsAnswer(mx_hosts=("mx",)))
    for email in ("x@mailinator.com", "x@eu.mailinator.com"):
        assert address_service.check(email, resolver=stub) == (AddressState.INVALID, AddressReason.DISPOSABLE)
    assert stub.domains == []


def test_verify_stores_the_status(db, resolver):
    row = address_service.verify(" JAN@example-shop-1.test ")
    assert (row.email, row.status, row.reason) == (EMAIL, AddressState.VALID, AddressReason.MX_OK)
    assert row.verified_at is not None and address_service.status_of(EMAIL) == row


def test_a_hard_bounce_is_final(db, resolver):
    address_service.mark_hard_bounce(EMAIL)
    row = address_service.verify(EMAIL)
    assert (row.status, row.reason) == (AddressState.INVALID, AddressReason.HARD_BOUNCE)
    assert resolver.domains == []


def test_stale_addresses_are_rechecked_and_a_hard_bounce_never(db, resolver):
    old = timezone.now() - timedelta(days=31)
    AddressStatus.objects.create(email="old@shop.test", status=AddressState.RISKY, verified_at=old)
    AddressStatus.objects.create(email="fresh@shop.test", status=AddressState.VALID, verified_at=timezone.now())
    AddressStatus.objects.create(
        email="gone@shop.test", status=AddressState.INVALID, reason=AddressReason.HARD_BOUNCE, verified_at=old
    )
    AddressStatus.objects.create(email="dns@shop.test", reason=AddressReason.DNS_ERROR, verified_at=timezone.now())

    assert set(address_service.stale(timezone.now()).values_list("email", flat=True)) == {
        "old@shop.test", "dns@shop.test",
    }  # fmt: skip
    assert address_service.reverify_stale(limit=1) == 1
    assert address_service.reverify_stale(limit=10) == 1
    assert sorted(resolver.domains) == ["shop.test", "shop.test"]
    assert AddressStatus.objects.get(email="old@shop.test").reason == AddressReason.MX_OK
    assert AddressStatus.objects.get(email="gone@shop.test").reason == AddressReason.HARD_BOUNCE


def test_reverify_task_does_nothing_while_verification_is_off(db, resolver, monkeypatch):
    AddressStatus.objects.create(email="old@shop.test")
    assert reverify_addresses() == 0
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_VERIFY_ENABLED", True)
    assert reverify_addresses() == 1


def test_can_mail_reasons_and_query_count(channel, django_assert_max_num_queries):
    with django_assert_max_num_queries(2):
        assert suppression_service.can_mail(channel, EMAIL) is None
    suppression_service.suppress_email(channel, EMAIL, source=SuppressionSource.MANUAL)
    with django_assert_max_num_queries(2):
        assert suppression_service.can_mail(channel, EMAIL) == "suppressed"
    address_service.mark_hard_bounce(EMAIL)
    assert suppression_service.can_mail(channel, EMAIL) == "address_invalid"
    assert suppression_service.can_mail(channel, anonymised_address(EMAIL)) == "suppressed"


def test_risky_and_unknown_do_not_block(channel):
    AddressStatus.objects.create(email="r@shop.test", status=AddressState.RISKY)
    AddressStatus.objects.create(email="u@shop.test", status=AddressState.UNKNOWN)
    assert suppression_service.can_mail(channel, "r@shop.test") is None
    assert suppression_service.can_mail(channel, "u@shop.test") is None


def test_live_smtp_hard_bounce_makes_the_next_communicate_address_invalid(policy, static_template):
    channel_service.set_mode(policy.channel, mode=ChannelMode.LIVE, live_enabled=True)
    message = approved_message(email=EMAIL)
    refused = smtplib.SMTPRecipientsRefused({EMAIL: (550, b"no such user")})
    with override_settings(ENVIRONMENT="production"), mock.patch(SEND, side_effect=refused):
        delivery_service.deliver(message, now=MONDAY_10)

    again = approved_message(email=EMAIL, subject_ref="send:2")

    assert (again.status, again.failure_detail) == (MessageStatus.SUPPRESSED, "address_invalid")
    assert AddressStatus.objects.get().reason == AddressReason.HARD_BOUNCE


def test_live_dsn_hard_bounce_marks_the_address(channel):
    go_live(channel)
    make_sent(channel, "owner@example-shop-6.test")
    ingest(channel, eml("dsn_hard.eml"))
    row = AddressStatus.objects.get()
    assert (row.email, row.status, row.reason) == ("owner@example-shop-6.test", "invalid", "hard_bounce")


def test_sandbox_bounce_changes_no_status(channel, sandbox):
    make_sent(channel, "owner@example-shop-6.test")
    ingest(channel, eml("dsn_hard.eml"))
    assert not AddressStatus.objects.exists()


def test_delivery_asks_again_before_the_claim(policy, sandbox, static_template):
    message = approved_message(email=EMAIL)
    address_service.mark_hard_bounce(EMAIL)

    assert delivery_service.deliver(message, now=MONDAY_10) == MessageStatus.SUPPRESSED
    message.refresh_from_db()
    assert (message.status, message.failure_detail) == (MessageStatus.SUPPRESSED, "address_invalid")


def test_communicate_verifies_an_unknown_address_when_enabled(static_template, verify_on):
    verify_on.answer = DnsAnswer(exists=False)

    message = approved_message(email=EMAIL)

    assert (message.status, message.failure_detail) == (MessageStatus.SUPPRESSED, "address_invalid")
    assert approved_message(email=EMAIL, subject_ref="send:2").status == MessageStatus.SUPPRESSED
    assert verify_on.domains == ["example-shop-1.test"]  # fresh: not checked twice


def test_communicate_rechecks_a_stale_address(static_template, verify_on):
    AddressStatus.objects.create(email=EMAIL, status="valid", verified_at=timezone.now() - timedelta(days=31))
    verify_on.answer = DnsAnswer(null_mx=True)
    assert approved_message(email=EMAIL).failure_detail == "address_invalid"


def test_a_token_address_is_never_verified(db, verify_on):
    address_service.verify_if_due(anonymised_address(EMAIL), timezone.now())
    assert verify_on.domains == [] and not AddressStatus.objects.exists()


def test_communicate_checks_nothing_while_verification_is_off(static_template, resolver):
    assert approved_message(email=EMAIL).status == MessageStatus.APPROVED
    assert resolver.domains == [] and not AddressStatus.objects.exists()


def test_explain_endpoint_names_both_cases(channel, admin_api, monkeypatch):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_SUPPRESSION_SCOPE", "install")
    suppression_service.suppress_email(channel, EMAIL, source=SuppressionSource.UNSUBSCRIBE_LINK)
    suppressed = admin_api.get(api_url(f"addresses/status/?email={EMAIL.upper()}")).json()
    address_service.mark_hard_bounce("gone@shop.test")
    invalid = admin_api.get(api_url("addresses/status/?email=gone@shop.test")).json()
    free = admin_api.get(api_url("addresses/status/?email=free@shop.test")).json()

    assert (suppressed["email"], suppressed["refusal"], suppressed["address_status"]) == (EMAIL, "suppressed", None)
    row = suppressed["suppressions"][0]
    assert (row["kind"], row["scope"], row["source"]) == ("email", "install", "unsubscribe_link")
    assert (invalid["refusal"], invalid["suppressions"]) == ("address_invalid", [])
    assert (invalid["address_status"]["status"], invalid["address_status"]["reason"]) == ("invalid", "hard_bounce")
    assert (free["refusal"], free["suppressions"], free["address_status"]) == (None, [], None)
    assert admin_api.get(api_url("addresses/status/")).status_code == 400


def test_gdpr_erase_removes_the_status_row(channel):
    address_service.mark_hard_bounce(EMAIL)
    assert gdpr.gdpr_export(EMAIL)["AddressStatus"][0]["reason"] == "hard_bounce"

    gdpr.gdpr_erase(EMAIL)

    assert not AddressStatus.objects.exists()


def test_anonymisation_removes_the_status_row(channel):
    make_sent(channel, EMAIL, ref="leads.Company:7")
    address_service.mark_hard_bounce(EMAIL)
    address_service.mark_hard_bounce("colleague@example-shop-1.test")

    on_contact_anonymised(
        None, email_hash=email_hash(EMAIL), anonymised_email=anonymised_address(EMAIL), subject_ref="leads.Company:7"
    )

    assert list(AddressStatus.objects.values_list("email", flat=True)) == ["colleague@example-shop-1.test"]
