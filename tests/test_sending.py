# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import re
import smtplib
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
import redis
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from django_communicator.apps import DjangoCommunicatorConfig
from django_communicator.enums import ChannelMode, MessageStatus
from django_communicator.models import SendPolicy, Suppression
from django_communicator.services import (
    channel_service,
    clock_service,
    counter_service,
    mail_builder,
    review_service,
    send_service,
)
from django_communicator.services.communicate_service import communicate
from django_communicator.signals import message_sent
from django_communicator.tasks import send_due
from tests.conftest import CHANNEL_IDX, MONDAY_10, WARSAW, api_url, approved_message
from tests.factories import ChannelFactory

NOTIFY = "django_notifications.services.notify_service.notify"
SEND = "django.core.mail.EmailMultiAlternatives.send"


def _run() -> dict:
    return dict(send_service.run_send_due())


def test_C13_dry_run_would_send_no_smtp(policy, static_template, fake_redis):
    message = approved_message()

    counts = _run()

    message.refresh_from_db()
    assert (message.status, message.sent_at, message.message_id) == (MessageStatus.WOULD_SEND, MONDAY_10, "")
    assert (counts["would_send"], len(mail.outbox), counter_service.sent_on(policy.channel, MONDAY_10.date())) == (
        1,
        0,
        0,
    )


def test_C14_sandbox_redirects_with_original_to_and_prefix(
    policy, sandbox, static_template, django_capture_on_commit_callbacks
):
    message = approved_message()
    received = []
    message_sent.connect(lambda sender, message, **kw: received.append(message.pk), weak=False, dispatch_uid="t14")

    with django_capture_on_commit_callbacks(execute=True):
        counts = _run()

    message_sent.disconnect(dispatch_uid="t14")
    message.refresh_from_db()
    sent = mail.outbox[0]
    assert (counts["sent"], message.status, received) == (1, MessageStatus.SENT, [message.pk])
    assert (sent.to, sent.extra_headers["X-Original-To"]) == (["sandbox@mail.example.test"], "jan@example-shop-1.test")
    assert sent.subject == "[SANDBOX] Hi Jan" and message.message_id == sent.extra_headers["Message-ID"]
    assert counter_service.sent_on(policy.channel, MONDAY_10.date()) == 1


def test_C15_live_outside_production_refused_notify_critical_once(policy, static_template):
    channel_service.set_mode(policy.channel, mode=ChannelMode.LIVE, live_enabled=True)
    message = approved_message()

    with mock.patch(NOTIFY) as notify:
        _run()
        _run()

    message.refresh_from_db()
    assert (message.status, len(mail.outbox), notify.call_count) == (MessageStatus.APPROVED, 0, 1)
    assert notify.call_args.kwargs["severity"] == "critical"
    with override_settings(ENVIRONMENT="production"), mock.patch("django.utils.timezone.now", return_value=MONDAY_10):
        _run()
    assert mail.outbox[0].to == ["jan@example-shop-1.test"] and "X-Original-To" not in mail.outbox[0].extra_headers


def test_C18_once_backend_missing_fails_loud_and_second_run_exits(
    policy, sandbox, static_template, monkeypatch, once_backend
):
    message = approved_message()
    send_due.once_backend.raise_or_lock(send_due.get_key((), {}), timeout=60)

    second = send_due.apply_async()

    assert (second.state, second.result, len(mail.outbox)) == ("REJECTED", None, 0)
    send_due.app.conf.update(ONCE=None)
    with pytest.raises(ImproperlyConfigured):
        send_due.run()
    monkeypatch.setattr("django_communicator.settings.COMMUNICATOR_REQUIRE_ONCE_BACKEND", True)
    with pytest.raises(ImproperlyConfigured):
        DjangoCommunicatorConfig.ready(mock.Mock())
    message.refresh_from_db()
    assert message.status == MessageStatus.APPROVED


def test_C19_smtp_5xx_failed_suppression_4xx_retry_max_3(policy, sandbox, static_template):
    soft = approved_message()
    with mock.patch(SEND, side_effect=smtplib.SMTPResponseException(451, b"try later")):
        statuses = []
        for _ in range(3):
            _run()
            soft.refresh_from_db()
            statuses.append((soft.status, soft.send_attempts))
    assert statuses == [("scheduled", 1), ("scheduled", 2), ("failed", 3)]
    assert (soft.failure_code, soft.failure_detail) == ("smtp", "451")

    channel_service.set_mode(policy.channel, mode=ChannelMode.LIVE, live_enabled=True)
    hard = approved_message(email="gone@example-shop-2.test", subject_ref="send:2")
    refused = smtplib.SMTPRecipientsRefused({"gone@example-shop-2.test": (550, b"no such user")})
    production = override_settings(ENVIRONMENT="production")
    with (
        production,
        mock.patch("django.utils.timezone.now", return_value=MONDAY_10),
        mock.patch(SEND, side_effect=refused),
    ):
        _run()
    hard.refresh_from_db()
    assert (hard.status, hard.failure_detail) == ("failed", "550")
    assert Suppression.objects.filter(value="gone@example-shop-2.test", reason="smtp 550").exists()


def test_C32_mail_is_multipart_alternative_with_message_id(policy, sandbox, static_template):
    message = approved_message(footer="Data controller: Example Seller.")

    built = mail_builder.build(message).message()

    assert built.get_content_type() == "multipart/alternative"
    text, html = (part.get_payload(decode=True).decode() for part in built.get_payload())
    assert text.rstrip("\n") == "Following up.\n\n-- \nData controller: Example Seller."
    assert html.rstrip("\n") == "<p>Following up.</p><p>Data controller: Example Seller.</p>"
    assert re.fullmatch(rf"<communicator-{message.pk}-[0-9a-f]{{8}}@mail\.example\.test>", built["Message-ID"])
    assert built["From"] == "outreach@mail.example.test"


def test_smtp_not_configured_keeps_approved_and_notifies_once(policy, sandbox, static_template):
    message = approved_message()
    connection = "django_email.domain.EmailDomain.get_channel_smtp_connection_if_exists"

    with mock.patch(connection, return_value=None), mock.patch(NOTIFY) as notify:
        _run()
        _run()

    message.refresh_from_db()
    assert (message.status, notify.call_count, notify.call_args.kwargs["severity"]) == ("approved", 1, "high")


def test_only_send_due_calls_deliver():
    source = Path(send_service.__file__).parents[1]
    callers = {p.name for p in source.rglob("*.py") if re.search(r"(?<!def )\bdeliver\(", p.read_text())}
    assert callers == {"send_service.py"}


def test_C31_send_now_sets_scheduled_at_only(policy, sandbox, ai_template, toolbox, recipient, context, admin_api):
    saturday = MONDAY_10 + timedelta(days=5)
    clock_service.set_override(policy.channel, saturday)
    draft = communicate(
        channel_idx=CHANNEL_IDX, template_key="lead.cold.shop", recipient=recipient, context=context, subject_ref="c31"
    )
    review_service.accept(draft, user=admin_api.user)
    assert draft.scheduled_at == datetime(2026, 9, 21, 8, 0, tzinfo=WARSAW)
    listed = admin_api.get(api_url("messages/?status=approved")).json()["results"][0]

    response = admin_api.post(api_url(f"messages/{draft.pk}/send-now/"))

    draft.refresh_from_db()
    assert (response.status_code, draft.status, draft.scheduled_at, len(mail.outbox)) == (200, "approved", saturday, 0)
    assert listed["next_slot"] == "2026-09-21T08:00:00+02:00"
    assert send_service.run_send_due()["deferred"] == 1


def test_test_endpoints_absent_outside_development(policy, admin_api):
    with override_settings(ENVIRONMENT="production"):
        codes = [
            admin_api.post(api_url(path), {}, format="json").status_code for path in ("test/clock/", "test/send-due/")
        ]
    assert codes == [404, 404]


def test_dev_endpoints_drive_clock_and_both_beats(policy, sandbox, static_template, admin_api, once_backend):
    clock = admin_api.post(api_url("test/clock/"), {"iso_datetime": "2026-09-19T10:00:00"}, format="json")
    approved_message()

    body = admin_api.post(api_url("test/send-due/")).json()

    assert clock.json()["now"] == "2026-09-19T10:00:00+02:00"
    assert body == {"sent": 0, "would_send": 0, "suppressed": 0, "failed": 0, "deferred": 1, "follow_ups_scheduled": 0}


def test_channel_config_and_policy_api(policy, admin_api):
    refused = admin_api.patch(api_url("channel/"), {"mode": "sandbox", "sandbox_mailbox": ""}, format="json")
    window = {"start_time": "09:00", "end_time": "12:00"}
    put = admin_api.put(api_url("policy/"), {"daily_cap": 3, "spread": False, "windows": [window]}, format="json")

    assert refused.status_code == 409
    assert (put.status_code, put.json()["daily_cap"], put.json()["windows"][0]["end_time"]) == (200, 3, "12:00:00")
    assert put.json()["next_slot"] == "2026-09-14T10:00:00+02:00"
    assert admin_api.get(api_url("channel/")).json()["mode"] == "dry_run"


def test_sequence_api(channel, admin_api):
    body = {"key": "followup", "steps": [{"number": 1, "days_after_previous": 3, "template_key": "followup"}]}
    created = admin_api.post(api_url("sequences/"), body, format="json").json()
    text = admin_api.post(api_url(f"sequences/{created['id']}/texts/"), {"body": "TEST 1/6"}, format="json")

    assert admin_api.post(api_url("sequences/"), body, format="json").status_code == 409
    assert admin_api.get(api_url(f"sequences/{created['id']}/steps/")).json()["results"][0]["days_after_previous"] == 3
    assert (
        text.status_code,
        admin_api.get(api_url(f"sequences/{created['id']}/texts/")).json()["results"][0]["body"],
    ) == (
        201,
        "TEST 1/6",
    )


def test_recipient_suppressed_after_approval_is_not_sent(policy, sandbox, static_template):
    message = approved_message()
    Suppression.objects.create(channel=policy.channel, kind="email", value="jan@example-shop-1.test")

    counts = _run()

    message.refresh_from_db()
    assert (counts["suppressed"], message.status, len(mail.outbox)) == (1, "suppressed", 0)


def test_counter_failure_keeps_the_sent_status(policy, sandbox, static_template):
    message = approved_message()

    with mock.patch.object(counter_service, "increment", side_effect=redis.ConnectionError):
        _run()

    message.refresh_from_db()
    assert (message.status, len(mail.outbox)) == ("sent", 1)


def test_broken_channel_does_not_stop_the_others(policy, sandbox, static_template):
    other = ChannelFactory(idx="other-channel", label="Other", country="XX")
    SendPolicy.objects.create(channel=other)
    message = approved_message()

    _run()

    message.refresh_from_db()
    assert message.status == "sent"
