# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Outbound Q3 (sender routes) and the per-mailbox cap with warm-up (Q8 in code)."""

from datetime import date
from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from django_communicator.enums import ChannelMode, FailureCode, MessageStatus
from django_communicator.models import Message, SenderRoute, Thread
from django_communicator.services import (
    channel_service,
    counter_service,
    send_service,
    sender_route_service,
)
from django_communicator.services.communicate_service import RecipientData, communicate
from tests.conftest import CHANNEL_IDX, MONDAY_10, api_url

ECOM = {"name": "Wiktor W.", "from_email": "wiktorw@ecom.house", "cc_email": "wiktorw@entirius.com"}
ENTIRIUS = {"from_email": "michalk@entirius.com", "cc_email": "michalk@ecom.house"}


def _sent_message(route: str = "", email: str = "jan@example-shop-1.test", subject_ref: str = "send:1") -> Message:
    recipient = RecipientData(email=email, first_name="Jan", language="pl")
    return communicate(
        channel_idx=CHANNEL_IDX,
        template_key="followup",
        recipient=recipient,
        context={},
        subject_ref=subject_ref,
        requires_review=False,
        sender_route=route,
    )


def _live_run() -> dict:
    with override_settings(ENVIRONMENT="production"), mock.patch("django.utils.timezone.now", return_value=MONDAY_10):
        return dict(send_service.run_send_due())


@pytest.fixture
def live(policy):
    return channel_service.set_mode(policy.channel, mode=ChannelMode.LIVE, live_enabled=True)


@pytest.fixture
def routes(channel):
    return (
        sender_route_service.create_route(channel, key="ecomhouse", **ECOM),
        sender_route_service.create_route(channel, key="entirius", **ENTIRIUS),
    )


def test_Q3_live_mail_leaves_from_the_route_with_the_dw_mailbox_in_cc(live, static_template, routes):
    _sent_message("ecomhouse")

    assert _live_run()["sent"] == 1

    sent = mail.outbox[0]
    assert (sent.from_email, sent.to, sent.cc) == (
        '"Wiktor W." <wiktorw@ecom.house>',
        ["jan@example-shop-1.test"],
        ["wiktorw@entirius.com"],
    )
    assert sent.extra_headers["Message-ID"].endswith("@ecom.house>")


def test_Q3_without_a_key_the_channel_default_route_is_used(live, static_template, routes, channel):
    sender_route_service.set_default(channel, "entirius")
    _sent_message()

    _live_run()

    assert (mail.outbox[0].from_email, mail.outbox[0].cc) == ("michalk@entirius.com", ["michalk@ecom.house"])


def test_Q3_no_key_and_no_default_keeps_the_channel_from_and_no_cc(live, static_template, routes):
    _sent_message()

    _live_run()

    assert mail.outbox[0].cc == [] and "ecom.house" not in mail.outbox[0].from_email


def test_Q3_sandbox_never_copies_the_real_dw_mailbox(policy, sandbox, static_template, routes):
    _sent_message("ecomhouse")

    send_service.run_send_due()

    sent = mail.outbox[0]
    assert (sent.to, sent.cc, sent.extra_headers["X-Original-Cc"]) == (
        ["sandbox@mail.example.test"],
        [],
        "wiktorw@entirius.com",
    )
    assert sent.from_email == '"Wiktor W." <wiktorw@ecom.house>'


def test_Q3_unknown_or_inactive_route_fails_with_a_readable_detail_and_sends_nothing(live, static_template, routes):
    unknown, inactive = _sent_message("nope", subject_ref="a"), _sent_message("entirius", subject_ref="b")
    SenderRoute.objects.filter(key="entirius").update(is_active=False)

    counts = _live_run()

    unknown.refresh_from_db(), inactive.refresh_from_db()
    assert (counts["failed"], len(mail.outbox)) == (2, 0)
    assert (unknown.status, unknown.failure_code) == (MessageStatus.FAILED, FailureCode.SENDER_ROUTE)
    assert "nope" in unknown.failure_detail and inactive.failure_code == FailureCode.SENDER_ROUTE


def test_Q3_a_failed_route_gives_the_cap_reservation_back(live, static_template, routes, policy):
    _sent_message("nope")

    _live_run()

    assert counter_service.sent_on(policy.channel, MONDAY_10.date()) == 0


def test_Q3_thread_keeps_the_first_route_and_follow_ups_use_it(live, static_template, routes):
    first = _sent_message("ecomhouse")
    again = _sent_message("entirius")  # same subject_ref and recipient: the open thread is reused

    assert first.thread_id == again.thread_id
    thread = Thread.objects.get(pk=first.thread_id)
    assert thread.sender_route == "ecomhouse"


def test_Q3_accept_refuses_an_unusable_route_with_a_409(draft_with_route, admin_api):
    response = admin_api.post(api_url(f"review/{draft_with_route.pk}/accept/"))

    assert response.status_code == 409
    assert "sender route nope" in response.json()["message"] or "sender route nope" in str(response.json())


@pytest.fixture
def draft_with_route(ai_template, toolbox, recipient, context):
    return communicate(
        channel_idx=CHANNEL_IDX,
        template_key="lead.cold.shop",
        recipient=recipient,
        context=context,
        subject_ref="bdd:42",
        sender_route="nope",
    )


def test_Q3_routes_crud_and_channel_default(admin_api, channel):
    created = admin_api.post(api_url("routes/"), {"key": "ecomhouse", **ECOM}, format="json")
    assert created.status_code == 201 and created.json()["cc_email"] == "wiktorw@entirius.com"
    assert admin_api.post(api_url("routes/"), {"key": "ecomhouse", **ECOM}, format="json").status_code == 409

    bad = admin_api.patch(api_url("channel/"), {"default_sender_route": "ghost"}, format="json")
    assert bad.status_code == 409
    ok = admin_api.patch(api_url("channel/"), {"default_sender_route": "ecomhouse"}, format="json")
    assert ok.json()["default_sender_route"] == "ecomhouse"
    assert admin_api.get(api_url("routes/")).json()["results"][0]["is_default"] is True

    assert admin_api.delete(api_url("routes/ecomhouse/")).status_code == 409
    admin_api.patch(api_url("channel/"), {"default_sender_route": ""}, format="json")
    replaced = admin_api.put(api_url("routes/ecomhouse/"), {**ECOM, "cc_email": ""}, format="json")
    assert replaced.json()["cc_email"] == ""
    assert admin_api.delete(api_url("routes/ecomhouse/")).status_code == 204
    assert admin_api.get(api_url("routes/ecomhouse/")).status_code == 404


def test_Q3_routes_are_staff_only_and_validated(customer_api, admin_api, channel):
    assert customer_api.get(api_url("routes/")).status_code == 403
    bad_mail = admin_api.post(api_url("routes/"), {"key": "x", "from_email": "not-an-address"}, format="json")
    assert bad_mail.status_code == 400
    bad_key = admin_api.post(api_url("routes/"), {"key": "Has Space", **ECOM}, format="json")
    assert bad_key.status_code == 400


def test_Q8_mailbox_cap_defers_the_rest_and_resets_per_day(live, static_template, channel):
    sender_route_service.create_route(channel, key="ecomhouse", daily_cap=1, **ECOM)
    first, second = (
        _sent_message("ecomhouse", subject_ref="a"),
        _sent_message("ecomhouse", email="eva@example-shop-2.test", subject_ref="b"),
    )

    counts = _live_run()

    first.refresh_from_db(), second.refresh_from_db()
    assert (counts["sent"], counts["deferred"]) == (1, 1)
    assert (first.status, second.status) == (MessageStatus.SENT, MessageStatus.APPROVED)
    assert counter_service.mailbox_sent_on("wiktorw@ecom.house", MONDAY_10.date()) == 1
    assert counter_service.sent_on(channel, MONDAY_10.date()) == 1  # the deferred one gave its place back


def test_Q8_mailbox_cap_is_shared_by_routes_with_one_from_address(live, static_template, channel):
    sender_route_service.create_route(channel, key="a", daily_cap=1, **ECOM)
    sender_route_service.create_route(channel, key="b", daily_cap=1, **ECOM)
    _sent_message("a", subject_ref="a"), _sent_message("b", email="eva@example-shop-2.test", subject_ref="b")

    assert _live_run()["sent"] == 1


def test_Q8_warmup_cap_grows_by_the_step_up_to_the_ceiling(channel):
    route = sender_route_service.create_route(
        channel,
        key="w",
        daily_cap=12,
        warmup_start_cap=5,
        warmup_step=3,
        warmup_started_on=date(2026, 9, 10),
        **ECOM,
    )
    caps = [sender_route_service.effective_cap(route, date(2026, 9, day)) for day in (9, 10, 11, 12, 13, 20)]

    assert caps == [5, 5, 8, 11, 12, 12]


def test_Q8_warmup_is_off_by_default_and_needs_a_ceiling(admin_api, channel):
    plain = admin_api.post(api_url("routes/"), {"key": "p", **ECOM}, format="json").json()
    assert (plain["cap_today"], plain["warmup_started_on"]) == (None, None)

    no_ceiling = admin_api.post(api_url("routes/"), {"key": "q", "warmup_start_cap": 5, **ECOM}, format="json")
    assert no_ceiling.status_code == 409

    on = admin_api.post(api_url("routes/"), {"key": "r", "daily_cap": 30, "warmup_start_cap": 5, **ECOM}, format="json")
    assert on.status_code == 201
    body = on.json()
    assert body["cap_today"] == 5 and body["warmup_started_on"] is not None

    off = admin_api.put(api_url("routes/r/"), {"daily_cap": 30, **ECOM}, format="json").json()
    assert (off["warmup_start_cap"], off["warmup_started_on"], off["cap_today"]) == (None, None, 30)


def test_Q8_policy_lists_the_mailbox_caps(admin_api, policy, routes):
    SenderRoute.objects.filter(key="ecomhouse").update(daily_cap=7)

    mailboxes = admin_api.get(api_url("policy/")).json()["mailboxes"]

    by_route = {m["route"]: m for m in mailboxes}
    assert by_route["ecomhouse"]["cap_today"] == 7 and by_route["entirius"]["cap_today"] is None


def test_Q3_reply_lands_in_the_same_thread_whichever_mailbox_delivered_it(channel):
    """Matching is by our Message-ID in In-Reply-To, not by mailbox: the same reply read from the From mailbox and
    from the CC mailbox (both forwarded to the polled one) is one Reply, and our own CC copy is dropped."""
    from django_communicator.models import Reply
    from django_communicator.services.inbound_service import ingest
    from tests.test_inbound import eml, make_sent

    message = make_sent(channel, "owner@example-shop-1.test")
    Thread.objects.filter(pk=message.thread_id).update(sender_route="ecomhouse")

    with mock.patch("django_notifications.services.notify_service.notify"):
        first = ingest(channel, eml("reply_plain.eml"))
        again = ingest(channel, eml("reply_plain.eml"))  # the copy that came in through the DW mailbox

    assert first.pk == again.pk and Reply.objects.count() == 1
    assert first.thread_id == message.thread_id
