# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Outbound Q6: one-click unsubscribe (RFC 8058) and the enforced legal footer."""

from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from django_communicator import settings as communicator_settings
from django_communicator.enums import ChannelMode, FailureCode, MessageStatus
from django_communicator.models import Message, Suppression
from django_communicator.services import channel_service, send_service, suppression_service, unsubscribe_service
from django_communicator.services.communicate_service import RecipientData, communicate
from django_communicator.signals import optout_confirmed
from tests.conftest import CHANNEL_IDX, MONDAY_10, approved_message
from tests.factories import make_static

BASE = "https://api.example.test"
EMAIL = "jan@example-shop-1.test"


@pytest.fixture
def unsubscribe_on():
    with mock.patch.object(communicator_settings, "COMMUNICATOR_UNSUBSCRIBE_BASE_URL", BASE):
        yield


def _path(token: str) -> str:
    return f"/api/communicator/v2/public/unsubscribe/{token}/"


def _live_run() -> dict:
    with override_settings(ENVIRONMENT="production"), mock.patch("django.utils.timezone.now", return_value=MONDAY_10):
        return dict(send_service.run_send_due())


def test_Q6_headers_and_link_are_added_when_a_base_url_is_set(policy, sandbox, static_template, unsubscribe_on):
    approved_message(footer="Legal text.")

    send_service.run_send_due()

    sent = mail.outbox[0]
    url = f"{BASE}{_path(unsubscribe_service.make_token(CHANNEL_IDX, EMAIL))}"
    assert sent.extra_headers["List-Unsubscribe"] == f"<{url}>"
    assert sent.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert url in sent.body and url in sent.alternatives[0].content
    # on the wire the URL stays one plain line: Python's SMTP policy would otherwise turn it into encoded words
    wire = sent.message().as_bytes().decode().replace("\n", "\r\n")
    assert f"List-Unsubscribe: <{url}>\r\n" in wire and "=?utf-8?" not in wire.split("\r\n\r\n")[0]


def test_Q6_nothing_is_added_by_default(policy, sandbox, static_template):
    approved_message()

    send_service.run_send_due()

    assert "List-Unsubscribe" not in mail.outbox[0].extra_headers and "unsubscribe" not in mail.outbox[0].body.lower()


def test_Q6_post_suppresses_the_address_and_is_idempotent(client, channel, static_template):
    token = unsubscribe_service.make_token(CHANNEL_IDX, EMAIL)
    heard = []
    optout_confirmed.connect(lambda sender, **kw: heard.append(kw), weak=False, dispatch_uid="q6")

    try:
        first = client.post(
            _path(token), "List-Unsubscribe=One-Click", content_type="application/x-www-form-urlencoded"
        )
        second = client.post(_path(token))
    finally:
        optout_confirmed.disconnect(dispatch_uid="q6")

    assert (first.status_code, second.status_code) == (200, 200)
    assert suppression_service.is_suppressed(channel, EMAIL)
    assert Suppression.objects.filter(channel=channel, value=EMAIL, reason="unsubscribe link").count() == 1


def test_Q6_unsubscribe_stops_the_sequence_and_tells_the_owner(
    client, channel, static_template, django_capture_on_commit_callbacks
):
    message = approved_message(subject_ref="leads.Company:7")
    heard = []
    optout_confirmed.connect(lambda sender, **kw: heard.append(kw), weak=False, dispatch_uid="q6b")

    try:
        with django_capture_on_commit_callbacks(execute=True):
            client.post(_path(unsubscribe_service.make_token(CHANNEL_IDX, "jan@example-shop-1.test")))
    finally:
        optout_confirmed.disconnect(dispatch_uid="q6b")

    assert [(h["subject_ref"], h["email"], h["channel_idx"]) for h in heard] == [
        ("leads.Company:7", EMAIL, CHANNEL_IDX)
    ]
    message.refresh_from_db()


def test_Q6_get_shows_a_page_and_changes_nothing(client, channel):
    response = client.get(_path(unsubscribe_service.make_token(CHANNEL_IDX, EMAIL)))

    assert (
        response.status_code == 200 and b"<form method=post>" in response.content and EMAIL.encode() in response.content
    )
    assert not suppression_service.is_suppressed(channel, EMAIL)


def test_Q6_forged_or_foreign_tokens_are_refused(client, channel):
    token = unsubscribe_service.make_token(CHANNEL_IDX, EMAIL)

    assert client.post(_path(token[:-2] + "xx")).status_code == 400
    assert client.post(_path("garbage")).status_code == 400
    assert client.get(_path("garbage")).status_code == 404
    assert client.post(_path(unsubscribe_service.make_token("no-such-channel", EMAIL))).status_code == 400
    assert Suppression.objects.count() == 0


def test_Q6_token_page_escapes_the_address(client, channel):
    token = unsubscribe_service.make_token(CHANNEL_IDX, "<script>x</script>@example-shop-1.test")

    assert b"<script>" not in client.get(_path(token)).content


def test_Q6_unsubscribed_address_is_not_sent_to_later(policy, sandbox, static_template, client):
    message = approved_message()
    client.post(_path(unsubscribe_service.make_token(CHANNEL_IDX, EMAIL)))

    send_service.run_send_due()

    message.refresh_from_db()
    assert (message.status, len(mail.outbox)) == (MessageStatus.SUPPRESSED, 0)


def test_Q6_message_that_requires_a_legal_footer_and_has_none_does_not_leave(policy, channel):
    make_static(channel, key="followup", requires_legal_footer=True, auto_approve=True)
    channel_service.set_mode(channel, mode=ChannelMode.LIVE, live_enabled=True)
    message = approved_message(footer="Legal text.")
    Message.objects.filter(pk=message.pk).update(legal_footer="  ")

    counts = _live_run()

    message.refresh_from_db()
    assert (counts["failed"], len(mail.outbox)) == (1, 0)
    assert (message.status, message.failure_code) == (MessageStatus.FAILED, FailureCode.LEGAL_FOOTER)
    assert "legal footer" in message.failure_detail


def test_Q6_with_the_footer_the_message_leaves_and_carries_it(policy, channel):
    make_static(channel, key="followup", requires_legal_footer=True, auto_approve=True)
    channel_service.set_mode(channel, mode=ChannelMode.LIVE, live_enabled=True)
    approved_message(footer="Legal text.")

    assert _live_run()["sent"] == 1
    assert "Legal text." in mail.outbox[0].body


def test_Q6_accept_refuses_a_draft_without_the_required_footer(admin_api, channel):
    make_static(channel, key="followup", requires_legal_footer=True)
    recipient = RecipientData(email=EMAIL, first_name="Jan", legal_footer="Legal text.")
    draft = communicate(
        channel_idx=CHANNEL_IDX, template_key="followup", recipient=recipient, context={}, subject_ref="x:1"
    )
    Message.objects.filter(pk=draft.pk).update(legal_footer="")

    response = admin_api.post(f"/api/communicator/v2/admin/{CHANNEL_IDX}/review/{draft.pk}/accept/")

    assert response.status_code == 409 and "legal footer" in str(response.json())
