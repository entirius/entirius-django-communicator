# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Gmail API as a transport and as an inbox (outbound): OAuth connect, send through a route, needs_reauth, replies.

Everything talks to the local stub (tests/gmail_stub.py); the `gmail` fixture is what points the communicator at it."""

import email
import logging
from email import policy as email_policy
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.core import mail
from django.db import connection
from django.test import Client, override_settings

from django_communicator import settings as communicator_settings
from django_communicator.enums import (
    ChannelMode,
    FailureCode,
    MailAccountState,
    MailTransport,
    MessageStatus,
    ReplyKind,
)
from django_communicator.models import MailAccount, Message, Reply
from django_communicator.services import (
    channel_service,
    gmail_client,
    gmail_inbound_service,
    mail_account_service,
    send_service,
    sender_route_service,
)
from django_communicator.services.communicate_service import RecipientData, communicate
from tests.conftest import CHANNEL_IDX, GMAIL_CALLBACK, MONDAY_10, api_url

ECOM = "wiktorw@ecom.house"
READ_SCOPE = gmail_client.SCOPE_READ
TOKENS = ("at-", "rt-")


def stub_get(stub, path, **params):
    return httpx.get(stub.url + path, params=params).json()


def stub_post(stub, path, **body):
    return httpx.post(stub.url + path, json=body).json()


def sent_raw(stub, address=ECOM):
    return [
        email.message_from_string(item["raw"], policy=email_policy.default)
        for item in stub_get(stub, "/__stub/sent", email=address)["sent"]
    ]


def consent(stub, account, *, user: Client | None = None, **client_args) -> "httpx.Response":
    """The browser part of the flow: open the consent URL at the stub, follow its redirect into the callback."""
    url, _ = mail_account_service.start_connect(account)
    redirect = httpx.get(url, follow_redirects=False)
    assert redirect.status_code == 302, redirect.text
    location = urlsplit(redirect.headers["location"])
    return Client().get(GMAIL_CALLBACK, {k: v[0] for k, v in parse_qs(location.query).items()})


@pytest.fixture
def account(channel, gmail):
    return mail_account_service.create_account(channel, email=ECOM)


@pytest.fixture
def connected(account, gmail):
    assert consent(gmail, account).status_code == 200
    account.refresh_from_db()
    return account


@pytest.fixture
def live(policy):
    return channel_service.set_mode(policy.channel, mode=ChannelMode.LIVE, live_enabled=True)


@pytest.fixture
def route(channel, connected):
    return sender_route_service.create_route(
        channel,
        key="ecomhouse",
        name="Wiktor W.",
        from_email=ECOM,
        cc_email="wiktorw@entirius.com",
        transport=MailTransport.GMAIL_API,
        mail_account=connected,
    )


def queue(route_key="ecomhouse", to="jan@example-shop-1.test", ref="send:1") -> Message:
    recipient = RecipientData(email=to, first_name="Jan", language="pl")
    return communicate(
        channel_idx=CHANNEL_IDX,
        template_key="followup",
        recipient=recipient,
        context={},
        subject_ref=ref,
        requires_review=False,
        sender_route=route_key,
    )


def run() -> dict:
    with override_settings(ENVIRONMENT="production"), mock.patch("django.utils.timezone.now", return_value=MONDAY_10):
        return dict(send_service.run_send_due())


# --- connect --------------------------------------------------------------------------------------------------------


def test_connect_stores_an_encrypted_token_and_asks_only_for_the_send_scope(account, gmail):
    response = consent(gmail, account)

    account.refresh_from_db()
    assert response.status_code == 200 and account.state == MailAccountState.CONNECTED
    auth = stub_get(gmail, "/__stub/last_auth")
    assert (auth["access_type"], auth["prompt"], auth["code_challenge_method"]) == ("offline", "consent", "S256")
    assert set(auth["scope"].split()) == {"openid", "email", gmail_client.SCOPE_SEND}  # no read scope without replies
    assert auth["login_hint"] == ECOM and not any(READ_SCOPE in scope for scope in account.scopes.split())
    with connection.cursor() as cursor:
        cursor.execute("select refresh_token from django_communicator_mailaccount where id = %s", [account.pk])
        stored = cursor.fetchone()[0]
    assert account.refresh_token.startswith("rt-") and "rt-" not in stored  # plaintext only in memory


def test_connect_with_replies_adds_the_read_scope_and_starts_the_cursor(channel, gmail):
    account = mail_account_service.create_account(channel, email=ECOM, inbound_enabled=True)

    consent(gmail, account)

    account.refresh_from_db()
    assert READ_SCOPE in account.scopes.split() and account.history_id


def test_api_never_returns_a_token(connected, admin_api):
    body = admin_api.get(api_url(f"accounts/{connected.pk}/")).content.decode()
    listing = admin_api.get(api_url("accounts/")).content.decode()

    assert "rt-" not in body + listing and "at-" not in body + listing and "refresh" not in (body + listing).lower()
    assert admin_api.get(api_url(f"accounts/{connected.pk}/")).json()["can_send"] is True


def test_signed_in_as_another_google_account_is_refused_and_the_token_revoked(account, gmail):
    stub_post(gmail, "/__stub/config", identity_override="someone.else@example.test")

    response = consent(gmail, account)

    account.refresh_from_db()
    assert response.status_code == 400 and "different Google account" in response.content.decode()
    assert (account.state, account.refresh_token, account.last_error) == (
        MailAccountState.NOT_CONNECTED,
        "",
        "account_mismatch",
    )
    assert "POST /revoke" in stub_get(gmail, "/__stub/calls")["calls"]


def test_a_refused_send_permission_is_not_a_connection(account, gmail):
    stub_post(gmail, "/__stub/config", drop_scopes=[gmail_client.SCOPE_SEND])

    response = consent(gmail, account)

    account.refresh_from_db()
    assert response.status_code == 400 and account.state == MailAccountState.NOT_CONNECTED
    assert account.last_error == "scope_missing"


def test_state_is_signed_single_use_and_expires(account, gmail):
    url, _ = mail_account_service.start_connect(account)
    query = {k: v[0] for k, v in parse_qs(urlsplit(httpx.get(url).headers["location"]).query).items()}

    assert Client().get(GMAIL_CALLBACK, {**query, "state": query["state"] + "x"}).status_code == 400
    assert Client().get(GMAIL_CALLBACK, query).status_code == 200
    assert Client().get(GMAIL_CALLBACK, query).status_code == 400  # replay
    assert Client().get(GMAIL_CALLBACK, {"error": "access_denied", "state": query["state"]}).status_code == 400


def test_connect_without_the_oauth_client_settings_is_a_clear_409(account, gmail, monkeypatch, admin_api):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_GMAIL_CLIENT_SECRET", "")

    response = admin_api.post(api_url(f"accounts/{account.pk}/connect/"))

    assert response.status_code == 409 and response.json()["error"] == "GMAIL_NOT_CONFIGURED"


def test_connect_endpoint_returns_the_consent_url(account, gmail, admin_api):
    response = admin_api.post(api_url(f"accounts/{account.pk}/connect/"))

    assert response.status_code == 200 and response.json()["authorization_url"].startswith(gmail.url + "/auth?")


def test_disconnect_revokes_and_forgets_the_token(connected, gmail, admin_api):
    token = connected.refresh_token

    response = admin_api.post(api_url(f"accounts/{connected.pk}/disconnect/"))

    connected.refresh_from_db()
    assert response.json()["state"] == "not_connected" and connected.refresh_token == ""
    assert (
        httpx.post(
            gmail.url + "/token",
            data={"grant_type": "refresh_token", "refresh_token": token, "client_id": "a", "client_secret": "b"},
        ).status_code
        == 400
    )


def test_test_connection_checks_the_token_and_the_profile(channel, gmail, admin_api):
    account = mail_account_service.create_account(channel, email=ECOM, inbound_enabled=True)
    consent(gmail, account)

    body = admin_api.post(api_url(f"accounts/{account.pk}/test/")).json()

    assert (body["ok"], body["checks"]) == (True, ["token", "profile"])


def test_test_connection_of_a_revoked_account_marks_needs_reauth(connected, gmail, admin_api):
    stub_post(gmail, "/__stub/revoke", email=ECOM)

    body = admin_api.post(api_url(f"accounts/{connected.pk}/test/")).json()

    assert (body["ok"], body["error"], body["account"]["state"]) == (False, "invalid_grant", "needs_reauth")


def test_test_send_goes_only_to_the_double(connected, gmail, admin_api, monkeypatch):
    ok = admin_api.post(api_url(f"accounts/{connected.pk}/test-send/"), {"to": "qa@example.test"}, format="json")
    assert ok.status_code == 200 and sent_raw(gmail)[0]["To"] == "qa@example.test"

    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_GMAIL_API_BASE", "https://gmail.googleapis.com")
    calls = len(stub_get(gmail, "/__stub/calls")["calls"])
    refused = admin_api.post(api_url(f"accounts/{connected.pk}/test-send/"), {"to": "qa@example.test"}, format="json")
    assert refused.status_code == 409 and refused.json()["error"] == "TEST_SEND_NEEDS_DOUBLE"
    assert len(stub_get(gmail, "/__stub/calls")["calls"]) == calls


# --- routes ---------------------------------------------------------------------------------------------------------


def test_route_needs_an_account_of_its_own_address(channel, connected, admin_api):
    base = {"key": "x", "from_email": "other@ecom.house", "transport": "gmail_api"}

    no_account = admin_api.post(api_url("routes/"), base, format="json")
    other_address = admin_api.post(api_url("routes/"), {**base, "mail_account_id": connected.pk}, format="json")
    ok = admin_api.post(
        api_url("routes/"), {**base, "from_email": ECOM, "mail_account_id": connected.pk}, format="json"
    )

    assert (no_account.status_code, other_address.status_code, ok.status_code) == (409, 409, 201)
    assert ok.json()["mail_account_state"] == "connected" and ok.json()["transport"] == "gmail_api"
    assert admin_api.delete(api_url(f"accounts/{connected.pk}/")).status_code == 409  # a route still uses it


def test_smtp_route_with_an_account_is_refused(channel, connected):
    with pytest.raises(sender_route_service.RouteConfigError):
        sender_route_service.create_route(channel, key="y", from_email=ECOM, mail_account=connected)


# --- sending --------------------------------------------------------------------------------------------------------


def test_live_gmail_route_sends_the_same_mail_through_the_api(live, static_template, route, gmail, monkeypatch):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_UNSUBSCRIBE_BASE_URL", "https://api.example.test")
    queue()

    counts = run()

    (sent,) = sent_raw(gmail)
    stored = Message.objects.get()
    assert counts["sent"] == 1 and mail.outbox == []  # no SMTP
    assert (sent["From"], sent["To"], sent["Cc"]) == (
        '"Wiktor W." <wiktorw@ecom.house>',
        "jan@example-shop-1.test",
        "wiktorw@entirius.com",
    )
    assert sent["Message-ID"] == stored.message_id and stored.message_id.endswith("@ecom.house>")
    assert sent["List-Unsubscribe"].startswith("<https://api.example.test/api/communicator/v2/public/unsubscribe/")
    assert sent["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "Wypisz" in sent.get_body(("plain",)).get_content()


def test_follow_up_carries_in_reply_to_and_references(live, static_template, route, gmail):
    first = queue()
    run()
    first.refresh_from_db()
    second = queue(ref="send:1")  # same thread
    assert second.thread_id == first.thread_id

    run()

    follow_up = sent_raw(gmail)[-1]
    assert follow_up["In-Reply-To"] == first.message_id and first.message_id in follow_up["References"]


def test_sandbox_with_the_double_sends_to_the_sandbox_mailbox_without_the_dw_copy(
    policy, sandbox, static_template, route, gmail
):
    queue()

    send_service.run_send_due()

    (sent,) = sent_raw(gmail)
    assert sent["To"] == "sandbox@mail.example.test" and sent["Subject"].startswith("[SANDBOX] ")
    assert sent["Cc"] is None and sent["X-Original-Cc"] == "wiktorw@entirius.com" and mail.outbox == []


def test_sandbox_facing_the_real_google_never_calls_it(policy, sandbox, static_template, route, gmail, monkeypatch):
    queue()
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_GMAIL_API_BASE", "https://gmail.googleapis.com")
    before = len(stub_get(gmail, "/__stub/calls")["calls"])

    send_service.run_send_due()

    assert len(stub_get(gmail, "/__stub/calls")["calls"]) == before  # nothing went to any API
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["sandbox@mail.example.test"]  # the SMTP sandbox, as before


def test_dry_run_makes_no_api_call(policy, static_template, route, gmail):
    queue()
    before = len(stub_get(gmail, "/__stub/calls")["calls"])

    send_service.run_send_due()

    assert Message.objects.get().status == MessageStatus.WOULD_SEND
    assert len(stub_get(gmail, "/__stub/calls")["calls"]) == before and sent_raw(gmail) == []


def test_a_revoked_account_pauses_its_route_and_other_routes_keep_sending(live, static_template, route, channel, gmail):
    sender_route_service.create_route(channel, key="entirius", from_email="michalk@entirius.com")
    waiting, other = queue(ref="a"), queue("entirius", to="ola@example-shop-2.test", ref="b")
    stub_post(gmail, "/__stub/revoke", email=ECOM)
    stub_post(gmail, "/__stub/expire_access", email=ECOM)
    from django.core.cache import cache

    cache.clear()

    first = run()
    again = run()

    waiting.refresh_from_db(), other.refresh_from_db(), route.mail_account.refresh_from_db()
    assert (
        route.mail_account.state == MailAccountState.NEEDS_REAUTH and route.mail_account.last_error == "invalid_grant"
    )
    assert (waiting.status, other.status) == (MessageStatus.APPROVED, MessageStatus.SENT)
    assert (
        first["sent"] == 1 and again["sent"] == 0 and len(mail.outbox) == 1
    )  # only the SMTP route; never via another mailbox
    assert sent_raw(gmail) == []


def test_after_a_new_sign_in_the_waiting_mail_goes_out(live, static_template, route, gmail):
    waiting = queue()
    stub_post(gmail, "/__stub/revoke", email=ECOM)
    from django.core.cache import cache

    cache.clear()
    run()
    assert (
        route.mail_account.state == MailAccountState.NEEDS_REAUTH
        or MailAccount.objects.get().state == MailAccountState.NEEDS_REAUTH
    )

    stub_post(gmail, "/__stub/reset")
    account = MailAccount.objects.get()
    assert consent(gmail, account).status_code == 200
    run()

    waiting.refresh_from_db()
    assert waiting.status == MessageStatus.SENT and len(sent_raw(gmail)) == 1


def test_needs_reauth_notifies_once(live, static_template, route, gmail):
    from django.core.cache import cache

    queue()
    stub_post(gmail, "/__stub/revoke", email=ECOM)
    cache.clear()
    with mock.patch("django_communicator.services.alert_service.notify_subject") as notify:
        run(), run()

    assert (
        notify.call_count == 1
        and ECOM in notify.call_args.kwargs["title"]
        and notify.call_args.kwargs["severity"] == "high"
    )


def test_rate_limit_is_retried_and_a_refusal_fails_the_message(live, static_template, route, gmail):
    stub_post(gmail, "/__stub/fail_send", status=429, reason="rateLimitExceeded", count=1)
    retried = queue(ref="a")
    run()
    retried.refresh_from_db()
    assert (retried.status, retried.send_attempts) == (MessageStatus.SCHEDULED, 1)

    run()
    retried.refresh_from_db()
    assert retried.status == MessageStatus.SENT

    stub_post(gmail, "/__stub/fail_send", status=400, reason="invalidArgument", count=1)
    refused = queue(to="bad@example-shop-3.test", ref="b")
    run()
    refused.refresh_from_db()
    assert (refused.status, refused.failure_code, refused.failure_detail) == (
        MessageStatus.FAILED,
        FailureCode.GMAIL_API,
        "invalidArgument",
    )


def test_a_stale_access_token_is_refreshed_once_and_the_send_repeated(live, static_template, route, gmail):
    queue()
    stub_post(gmail, "/__stub/expire_access", email=ECOM)  # the cached token is rejected with 401

    assert run()["sent"] == 1 and len(sent_raw(gmail)) == 1


def test_no_token_or_secret_reaches_a_log(live, static_template, route, gmail, caplog, account):
    caplog.set_level(logging.DEBUG)
    queue()
    stub_post(gmail, "/__stub/fail_send", status=500, reason="backendError", count=1)
    run(), run()
    stub_post(gmail, "/__stub/revoke", email=ECOM)
    from django.core.cache import cache

    cache.clear()
    queue(ref="z")
    run()

    text = caplog.text
    assert "rt-" not in text and "at-" not in text and "test-client-secret" not in text
    assert MailAccount.objects.get().last_error == "invalid_grant"


# --- replies --------------------------------------------------------------------------------------------------------


def reply_to(stub, original_id: str, *, subject="Re: Hi Jan", body="Yes, call me tomorrow.", mid="<r1@shop.test>"):
    raw = (
        f"From: Jan <jan@example-shop-1.test>\r\nTo: {ECOM}\r\nSubject: {subject}\r\nMessage-ID: {mid}\r\n"
        f"In-Reply-To: {original_id}\r\nReferences: {original_id}\r\nDate: Mon, 14 Sep 2026 12:00:00 +0200\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}\r\n"
    )
    return stub_post(stub, "/__stub/inject", email=ECOM, raw=raw)["id"]


@pytest.fixture
def receiving(channel, gmail):
    account = mail_account_service.create_account(channel, email=ECOM, inbound_enabled=True)
    consent(gmail, account)
    account.refresh_from_db()
    return account


@pytest.fixture
def receiving_route(channel, receiving):
    return sender_route_service.create_route(
        channel, key="ecomhouse", from_email=ECOM, transport=MailTransport.GMAIL_API, mail_account=receiving
    )


def test_a_reply_read_through_the_api_lands_in_the_thread_and_is_read_once(
    live, static_template, receiving_route, receiving, gmail
):
    sent = queue()
    run()
    sent.refresh_from_db()
    reply_to(gmail, sent.message_id)

    first = gmail_inbound_service.poll_all()
    second = gmail_inbound_service.poll_all()

    (reply,) = Reply.objects.all()
    assert (first["ingested"], second["ingested"]) == (1, 0)
    assert (reply.thread_id, reply.kind, reply.matched_by) == (sent.thread_id, ReplyKind.REPLY, "header")
    assert "call me tomorrow" in reply.body_text


def test_a_suspected_opt_out_arrives_through_the_same_pipeline(
    live, static_template, receiving_route, receiving, gmail
):
    from django_communicator.models import Suppression

    sent = queue()
    run()
    sent.refresh_from_db()
    reply_to(
        gmail, sent.message_id, subject="Re: Hi", body="Dziekuje, prosze nie pisać do nas więcej.", mid="<r2@shop.test>"
    )

    gmail_inbound_service.poll_all()

    assert Reply.objects.get().kind == ReplyKind.SUSPECTED_OPTOUT
    assert Suppression.objects.count() == 0  # a suspicion until a person confirms, exactly as for IMAP


def test_a_hard_bounce_report_read_through_the_api_suppresses_the_recipient(channel, receiving, gmail):
    from django_communicator.models import Suppression
    from tests.test_inbound import eml, go_live, make_sent

    go_live(channel)
    message = make_sent(channel, "owner@example-shop-6.test")
    stub_post(gmail, "/__stub/inject", email=ECOM, raw=eml("dsn_hard.eml").decode())

    gmail_inbound_service.poll_all()

    message.refresh_from_db()
    assert (message.status, message.failure_code) == (MessageStatus.FAILED, "bounce")
    assert Suppression.objects.get().value == "owner@example-shop-6.test"


def test_the_same_message_read_again_after_the_cursor_is_lost_is_not_duplicated(
    live, static_template, receiving_route, receiving, gmail
):
    sent = queue()
    run()
    sent.refresh_from_db()
    reply_to(gmail, sent.message_id)
    gmail_inbound_service.poll_all()
    MailAccount.objects.filter(pk=receiving.pk).update(history_id="1")  # as if the cursor went back

    gmail_inbound_service.poll_all()

    assert Reply.objects.count() == 1


def test_an_expired_history_id_falls_back_to_the_newest_inbox_mail(
    live, static_template, receiving_route, receiving, gmail
):
    sent = queue()
    run()
    sent.refresh_from_db()
    reply_to(gmail, sent.message_id)
    stub_post(gmail, "/__stub/config", history_expired_before=10**9)

    counts = gmail_inbound_service.poll_all()

    assert counts["ingested"] == 1 and Reply.objects.count() == 1
    stub_post(gmail, "/__stub/config", history_expired_before=0)
    assert MailAccount.objects.get().history_id not in {"", "1"}  # the cursor moved to the mailbox's current id


def test_polling_a_revoked_account_marks_it_and_does_not_raise(receiving, gmail):
    from django.core.cache import cache

    stub_post(gmail, "/__stub/revoke", email=ECOM)
    cache.clear()

    gmail_inbound_service.poll_all()

    receiving.refresh_from_db()
    assert receiving.state == MailAccountState.NEEDS_REAUTH


def test_an_account_without_replies_enabled_is_not_polled(connected, gmail):
    assert gmail_inbound_service.poll_all()["ingested"] == 0
    assert not any("/history" in call or "/messages" in call for call in stub_get(gmail, "/__stub/calls")["calls"][-3:])


def test_the_poll_task_covers_gmail_and_imap(receiving, gmail, once_backend):
    from django_communicator.tasks import poll_inbox

    assert poll_inbox.run() == {"ingested": 0, "skipped": 0, "quarantined": 0}


def test_real_google_hosts_are_recognised(monkeypatch):
    for url, real in {
        "https://gmail.googleapis.com": True,
        "https://www.googleapis.com": True,
        "http://127.0.0.1:9099": False,
        "http://gmail-api.invalid": False,
    }.items():
        monkeypatch.setattr(communicator_settings, "COMMUNICATOR_GMAIL_API_BASE", url)
        assert gmail_client.is_real_google() is real


def test_the_suite_default_does_not_resolve_to_google():
    assert not gmail_client.is_real_google() and ".invalid" in communicator_settings.COMMUNICATOR_GMAIL_API_BASE
