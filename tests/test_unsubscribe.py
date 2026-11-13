# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""One-click unsubscribe: token, headers and links of the mail, the public endpoint and its throttle."""

import email.policy
import re
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core import mail, signing
from django.core.cache import cache
from rest_framework.test import APIClient

from django_communicator import settings as communicator_settings
from django_communicator.enums import SequenceStopReason, SuppressionKind
from django_communicator.models import Sequence, Suppression, ThreadSequenceState
from django_communicator.services import mail_builder, send_service, unsubscribe_service
from django_communicator.signals import optout_confirmed
from tests.conftest import CHANNEL_IDX, approved_message

BASE = "https://api.example.test"
EMAIL = "jan@example-shop-1.test"


@pytest.fixture
def unsubscribe_on(monkeypatch):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_UNSUBSCRIBE_BASE_URL", BASE)


@pytest.fixture
def anon() -> APIClient:
    cache.clear()
    return APIClient()


def url(token: str) -> str:
    return f"/api/communicator/v2/public/unsubscribe/{token}/"


def token(email: str = EMAIL, channel_idx: str = CHANNEL_IDX) -> str:
    return unsubscribe_service.make_token(channel_idx, email)


def test_token_round_trip_and_tampering(channel):
    good = token("Jan@Example-Shop-1.test")

    assert unsubscribe_service.read_token(good) == (channel, EMAIL)
    with pytest.raises(unsubscribe_service.InvalidTokenError):
        unsubscribe_service.read_token(good[:-2] + "xx")
    with pytest.raises(unsubscribe_service.InvalidTokenError):
        unsubscribe_service.read_token(signing.dumps({"c": CHANNEL_IDX, "e": EMAIL}, salt="other"))
    with pytest.raises(unsubscribe_service.InvalidTokenError):
        unsubscribe_service.read_token(token(channel_idx="gone-channel"))


def test_mail_without_base_url_has_no_headers_and_no_link(policy, sandbox, static_template):
    built = mail_builder.build(approved_message(footer="Seller.")).message()

    assert built["List-Unsubscribe"] is None and built["List-Unsubscribe-Post"] is None
    assert "unsubscribe" not in built.as_string().lower()


def test_mail_carries_both_headers_as_single_lines_on_the_wire(policy, sandbox, static_template, unsubscribe_on):
    wire = mail_builder.build(approved_message(footer="Seller.")).message(policy=email.policy.SMTP).as_bytes()
    link = unsubscribe_service.url_for(CHANNEL_IDX, EMAIL)
    assert len(link) > 78
    assert f"\r\nList-Unsubscribe: <{link}>\r\n".encode() in wire
    assert b"\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n" in wire
    assert b"=?utf-8?" not in wire.split(b"\r\n\r\n")[0]


def test_unfolded_header_leaves_every_other_header_unchanged(policy, sandbox, static_template, unsubscribe_on):
    built = mail_builder.build(approved_message(footer="Seller."))

    def head(message) -> list[bytes]:
        wire = re.sub(rb'boundary="[^"]+"', b"", message.as_bytes())
        unfolded = wire.split(b"\r\n\r\n")[0].replace(b"\r\n ", b" ").split(b"\r\n")
        return [line for line in unfolded if not line.startswith((b"Date:", b"List-Unsubscribe:"))]

    ours = head(built.message(policy=email.policy.SMTP))
    standard = head(mail.EmailMultiAlternatives.message(built, policy=email.policy.SMTP))
    assert ours == standard and len(ours) > 5


@pytest.mark.parametrize(("language", "label"), [("PL", "Wypisz się z listy"), ("EN", "Unsubscribe")])
def test_link_in_text_and_html_with_body_language_label(
    policy, sandbox, static_template, unsubscribe_on, language, label
):
    message = approved_message(footer="Seller.")
    with mock.patch.object(mail_builder, "_body_language", return_value=language):
        built = mail_builder.build(message).message()

    text, html = (part.get_payload(decode=True).decode() for part in built.get_payload())
    link = unsubscribe_service.url_for(CHANNEL_IDX, EMAIL)
    assert text.rstrip("\n").endswith(f"Seller.\n\n{label}: {link}")
    assert f'<a href="{link}">{label}</a>' in html


def test_sent_mail_carries_the_headers(policy, sandbox, static_template, unsubscribe_on):
    approved_message(footer="Seller.")

    send_service.run_send_due()

    (sent,) = mail.outbox
    assert sent.extra_headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert sent.extra_headers["List-Unsubscribe"].startswith(f"<{BASE}/api/communicator/v2/public/unsubscribe/")


def test_get_shows_the_page_and_suppresses_nothing(channel, anon):
    response = anon.get(url(token()))

    assert response.status_code == 200 and b'<form method="post">' in response.content
    assert EMAIL.encode() in response.content and b"Wypisz mnie" in response.content
    assert not Suppression.objects.exists()


@pytest.fixture
def running(policy, sandbox, static_template):
    sequence = Sequence.objects.create(channel=policy.channel, key="followup")
    message = approved_message(footer="Seller.")
    return ThreadSequenceState.objects.create(thread=message.thread, sequence=sequence)


def test_post_suppresses_stops_the_sequence_signals_and_records_the_objection(
    running, anon, django_capture_on_commit_callbacks
):
    signals = []
    optout_confirmed.connect(lambda **kw: signals.append(kw), weak=False, dispatch_uid="unsub-test")
    fake = SimpleNamespace(record_objection=mock.Mock())
    try:
        with (
            mock.patch("django_communicator.services.optout_service._objection_service", return_value=fake),
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = anon.post(
                url(token()), "List-Unsubscribe=One-Click", content_type="application/x-www-form-urlencoded"
            )
    finally:
        optout_confirmed.disconnect(dispatch_uid="unsub-test")

    assert response.status_code == 200
    row = Suppression.objects.get()
    assert (row.channel_id, row.kind, row.value, row.reason) == (
        running.thread.channel_id,
        SuppressionKind.EMAIL,
        EMAIL,
        "unsubscribe link",
    )
    running.refresh_from_db()
    assert running.stop_reason == SequenceStopReason.OPTOUT
    assert [(s["subject_ref"], s["email"], s["channel_idx"]) for s in signals] == [("send:1", EMAIL, CHANNEL_IDX)]
    fake.record_objection.assert_called_once()


def test_second_post_is_harmless(running, anon, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        anon.post(url(token()))
    signals = []
    optout_confirmed.connect(lambda **kw: signals.append(kw), weak=False, dispatch_uid="unsub-test")
    try:
        with django_capture_on_commit_callbacks(execute=True):
            second = anon.post(url(token()))
    finally:
        optout_confirmed.disconnect(dispatch_uid="unsub-test")

    assert second.status_code == 200 and Suppression.objects.count() == 1 and signals == []


@pytest.mark.parametrize("bad", ["not-a-token", "eyJjIjoxfQ:abc:def"])
def test_bad_token_is_400_with_one_generic_message(channel, anon, bad):
    for response in (anon.get(url(bad)), anon.post(url(bad))):
        assert response.status_code == 400
        assert b"This unsubscribe link is not valid." in response.content
    assert not Suppression.objects.exists()


def test_throttle_answers_429(channel, anon):
    from django_communicator.api.public.throttling import UnsubscribeThrottle

    with mock.patch.object(UnsubscribeThrottle, "THROTTLE_RATES", {"communicator_unsubscribe": "2/hour"}):
        codes = [anon.get(url(token())).status_code for _ in range(3)]

    assert codes == [200, 200, 429]


@pytest.mark.parametrize("configured", [{}, {"communicator_unsubscribe": None}, {"communicator_unsubscribe": "x"}])
def test_throttle_falls_back_when_the_rate_is_missing_or_malformed(configured):
    from django_communicator.api.public.throttling import UnsubscribeThrottle

    with mock.patch.object(UnsubscribeThrottle, "THROTTLE_RATES", configured):
        assert UnsubscribeThrottle().get_rate() == UnsubscribeThrottle.fallback_rate
