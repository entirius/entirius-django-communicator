# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Suppression scope (channel / install), source, the copy command, the admin list with scope and source."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from django_communicator import settings as communicator_settings
from django_communicator.enums import MessageStatus, SuppressionKind, SuppressionSource
from django_communicator.models import AddressStatus, Suppression
from django_communicator.services import health_service, suppression_service, unsubscribe_service
from django_communicator.services.communicate_service import RecipientData, communicate
from django_communicator.utils.emails import anonymised_address
from tests.conftest import CHANNEL_IDX, MONDAY_10, api_url
from tests.factories import ChannelFactory, make_static
from tests.test_health import sent_mail

EMAIL = "jan@example-shop-1.test"
OTHER_IDX = "default-local"


@pytest.fixture
def install(monkeypatch):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_SUPPRESSION_SCOPE", "install")


@pytest.fixture
def two_channels(channel):
    other = ChannelFactory(idx=OTHER_IDX)
    make_static(channel)
    make_static(other)
    return channel, other


def send(channel_idx: str, email: str = EMAIL):
    recipient = RecipientData(email=email, first_name="Jan", language="pl")
    return communicate(
        channel_idx=channel_idx, template_key="followup", recipient=recipient, context={}, subject_ref="scope:1"
    )


def unsubscribe_in_first_channel() -> None:
    unsubscribe_service.unsubscribe(unsubscribe_service.make_token(CHANNEL_IDX, EMAIL))


def outcome(message) -> tuple[str, str]:
    return message.status, message.failure_detail


def test_channel_scope_isolates_two_channels(two_channels):
    unsubscribe_in_first_channel()

    assert outcome(send(CHANNEL_IDX)) == (MessageStatus.SUPPRESSED, "suppressed")
    assert send(OTHER_IDX).status == MessageStatus.REVIEW_REQUIRED
    row = Suppression.objects.get()
    assert (row.channel.idx, row.source) == (CHANNEL_IDX, SuppressionSource.UNSUBSCRIBE_LINK)


def test_install_scope_blocks_the_address_in_every_channel(two_channels, install):
    unsubscribe_in_first_channel()

    assert outcome(send(OTHER_IDX)) == (MessageStatus.SUPPRESSED, "suppressed")
    assert outcome(send(CHANNEL_IDX)) == (MessageStatus.SUPPRESSED, "suppressed")
    assert Suppression.objects.get().channel is None


def test_rows_keep_working_after_the_setting_changes(two_channels, monkeypatch):
    first, other = two_channels
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_SUPPRESSION_SCOPE", "install")
    suppression_service.create_suppression(first, kind=SuppressionKind.DOMAIN, value="global-shop.test")
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_SUPPRESSION_SCOPE", "channel")
    suppression_service.create_suppression(first, kind=SuppressionKind.EMAIL, value=EMAIL)

    assert suppression_service.can_mail(other, "anna@global-shop.test") == "suppressed"
    assert suppression_service.can_mail(other, EMAIL) is None
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_SUPPRESSION_SCOPE", "install")
    assert suppression_service.can_mail(other, EMAIL) is None  # a switch never rewrites rows
    assert suppression_service.can_mail(first, EMAIL) == "suppressed"


def test_unknown_scope_is_a_configuration_error(monkeypatch):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_SUPPRESSION_SCOPE", "everywhere")
    with pytest.raises(ImproperlyConfigured):
        suppression_service.scope()


def test_install_scope_duplicate_is_refused(channel, install):
    suppression_service.create_suppression(channel, kind="email", value=EMAIL)
    with pytest.raises(suppression_service.DuplicateSuppressionError):
        suppression_service.create_suppression(ChannelFactory(idx=OTHER_IDX), kind="email", value=EMAIL.upper())


def test_email_token_rows_are_never_added_by_hand(channel, admin_api):
    with pytest.raises(suppression_service.InvalidSuppressionValueError):
        suppression_service.create_suppression(channel, kind=SuppressionKind.EMAIL_TOKEN, value=EMAIL)
    response = admin_api.post(api_url("suppressions/"), {"kind": "email_token", "value": EMAIL}, format="json")
    assert response.status_code == 400 and not Suppression.objects.exists()


def test_writers_set_their_source(two_channels):
    first, _ = two_channels
    suppression_service.suppress_email(first, "a@shop.test", source=SuppressionSource.BOUNCE, reason="dsn 5.1.1")
    suppression_service.suppress_token("anon-1@anonymised.invalid", reason="gdpr_erase")
    rows = dict(Suppression.objects.values_list("value", "source"))
    assert rows == {"a@shop.test": SuppressionSource.BOUNCE, "anon-1@anonymised.invalid": SuppressionSource.ERASED}


@pytest.mark.django_db(transaction=True)
def test_only_known_kinds_may_be_global():
    with pytest.raises(IntegrityError), transaction.atomic():
        Suppression.objects.create(channel=None, kind="phone", value="123")


def test_command_copies_channel_rows_to_global_rows_idempotently(two_channels):
    first, other = two_channels
    suppression_service.create_suppression(first, kind="email", value=EMAIL, source=SuppressionSource.BOUNCE)
    suppression_service.create_suppression(other, kind="email", value=EMAIL)
    suppression_service.create_suppression(other, kind="domain", value="shop.test")
    out = StringIO()

    call_command("communicator_suppressions", "--to-install", stdout=out)
    assert "2 global suppression rows would be created" in out.getvalue()
    assert not Suppression.objects.filter(channel=None).exists()

    call_command("communicator_suppressions", "--to-install", "--apply", stdout=out)
    call_command("communicator_suppressions", "--to-install", "--apply", stdout=out)
    copied = dict(Suppression.objects.filter(channel=None).values_list("value", "source"))
    assert copied == {EMAIL: SuppressionSource.BOUNCE, "shop.test": SuppressionSource.MANUAL}
    assert out.getvalue().rstrip().endswith("0 global suppression rows created")


def test_list_shows_scope_and_source_and_filters(two_channels, admin_api, install):
    first, other = two_channels
    suppression_service.create_suppression(other, kind="email", value="global@shop.test")
    Suppression.objects.create(channel=first, kind="email", value="mine@shop.test", source=SuppressionSource.BOUNCE)
    Suppression.objects.create(channel=other, kind="email", value="theirs@shop.test")

    rows = admin_api.get(api_url("suppressions/")).json()["results"]
    by_scope = admin_api.get(api_url("suppressions/?scope=install")).json()["results"]
    by_source = admin_api.get(api_url("suppressions/?source=bounce")).json()["results"]
    by_search = admin_api.get(api_url("suppressions/?search=GLOB")).json()["results"]

    assert [(r["value"], r["scope"], r["source"]) for r in rows] == [
        ("global@shop.test", "install", "manual"), ("mine@shop.test", "channel", "bounce"),
    ]  # fmt: skip
    assert [r["value"] for r in by_scope] == [r["value"] for r in by_search] == ["global@shop.test"]
    assert [r["value"] for r in by_source] == ["mine@shop.test"]
    assert admin_api.get(api_url("suppressions/?scope=planet")).status_code == 400


def test_post_follows_the_scope_and_a_global_row_can_be_deleted(channel, admin_api, install):
    created = admin_api.post(api_url("suppressions/"), {"kind": "domain", "value": "shop.test"}, format="json")
    assert created.status_code == 201 and created.json()["scope"] == "install"

    assert admin_api.delete(api_url(f"suppressions/{created.json()['id']}/")).status_code == 204
    assert not Suppression.objects.exists()


def test_a_token_row_cannot_be_deleted(channel, admin_api):
    suppression_service.suppress_token("anon-1@anonymised.invalid", reason="gdpr_erase")
    row = Suppression.objects.get()
    assert admin_api.delete(api_url(f"suppressions/{row.pk}/")).status_code == 404


def test_health_counts_global_optouts_on_the_mailbox_that_sent(two_channels, install):
    first, other = two_channels
    mail = sent_mail(None, 1, channel=first)
    suppression_service.suppress_email(other, mail[0].thread.recipient_email, source=SuppressionSource.UNSUBSCRIBE_LINK)

    found = health_service.counts(first, MONDAY_10 - timedelta(days=1))

    assert found[None]["optouts"] == 1
    assert health_service.counts(other, MONDAY_10 - timedelta(days=1)) == {}


@pytest.mark.django_db(transaction=True)
def test_migration_sets_the_source_from_the_reason():
    app, before, after = "django_communicator", "0018_remove_mailbox_config", "0019_suppression_source_address_status"
    executor = MigrationExecutor(connection)
    executor.migrate([(app, before)])
    old = executor.loader.project_state([(app, before)]).apps.get_model(app, "Suppression")
    reasons = ["optout confirmed", "unsubscribe link", "dsn 5.1.1", "smtp 550", "complaint", "gdpr_erase", "asked"]
    old.objects.bulk_create(
        old(channel=None, kind="email", value=f"{n}@shop.test", reason=r) for n, r in enumerate(reasons)
    )

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([(app, after)])

    sources = list(Suppression.objects.order_by("value").values_list("source", flat=True))
    assert sources == ["optout_confirmed", "unsubscribe_link", "bounce", "bounce", "complaint", "erased", "manual"]
    executor = MigrationExecutor(connection)
    executor.migrate([(app, before)])  # the reverse drops the column and keeps the rows
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes(app))
    assert Suppression.objects.count() == len(reasons)


# --- many addresses at once ---


def test_can_mail_many_answers_like_can_mail_in_two_queries(channel, django_assert_num_queries):
    suppression_service.create_suppression(channel, kind=SuppressionKind.EMAIL, value="listed@shop.test", reason="x")
    suppression_service.create_suppression(channel, kind=SuppressionKind.DOMAIN, value="blocked.test", reason="x")
    suppression_service.suppress_token(anonymised_address("erased@shop.test"), reason="erased")
    AddressStatus.objects.create(email="bad@shop.test", status="invalid", reason="syntax")
    emails = ["Listed@Shop.test", "a@blocked.test", "erased@shop.test", "bad@shop.test", "free@shop.test", "nohost"]
    expected = {email: suppression_service.can_mail(channel, email) for email in emails}

    with django_assert_num_queries(2):
        answers = suppression_service.can_mail_many(channel, emails)

    assert answers == expected
    assert list(answers.values()) == ["suppressed", "suppressed", "suppressed", "address_invalid", None, None]
