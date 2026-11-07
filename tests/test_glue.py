# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Outbound glue (leads <-> communicator): the pre-send address check hook and the `message_bounced` signal."""

import smtplib
from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from django_communicator import settings as communicator_settings
from django_communicator.enums import ChannelMode, FailureCode, MessageStatus
from django_communicator.models import Message
from django_communicator.services import channel_service, delivery_service, send_service
from django_communicator.services.communicate_service import RecipientData, communicate
from django_communicator.signals import message_bounced
from tests.conftest import CHANNEL_IDX, MONDAY_10

CHECKED: list[str] = []


def deny_dead(email: str) -> str | None:
    CHECKED.append(email)
    return "email_invalid" if email.startswith("dead@") else None


def explode(email: str) -> str | None:
    raise RuntimeError("leads is down")


@pytest.fixture
def live(policy):
    return channel_service.set_mode(policy.channel, mode=ChannelMode.LIVE, live_enabled=True)


@pytest.fixture
def check_on():
    CHECKED.clear()
    with mock.patch.object(communicator_settings, "COMMUNICATOR_PRESEND_CHECK", "tests.test_glue.deny_dead"):
        yield


def _queue(email: str, ref: str = "x:1") -> Message:
    recipient = RecipientData(email=email, first_name="Jan", language="pl")
    return communicate(
        channel_idx=CHANNEL_IDX,
        template_key="followup",
        recipient=recipient,
        context={},
        subject_ref=ref,
        requires_review=False,
    )


def _live_run() -> dict:
    with override_settings(ENVIRONMENT="production"), mock.patch("django.utils.timezone.now", return_value=MONDAY_10):
        return dict(send_service.run_send_due())


def test_glue_refused_address_is_not_sent_and_has_a_readable_failure_code(live, static_template, check_on):
    dead, fine = _queue("dead@example-shop-1.test", "a"), _queue("ok@example-shop-2.test", "b")

    counts = _live_run()

    dead.refresh_from_db(), fine.refresh_from_db()
    assert (counts["failed"], counts["sent"]) == (1, 1)
    assert (dead.status, dead.failure_code, dead.failure_detail) == (
        MessageStatus.FAILED,
        FailureCode.PRESEND_CHECK,
        "email_invalid",
    )
    assert [m.to for m in mail.outbox] == [["ok@example-shop-2.test"]]
    assert "dead@example-shop-1.test" in CHECKED


def test_glue_without_the_setting_nothing_is_checked(live, static_template):
    _queue("dead@example-shop-1.test")

    assert _live_run()["sent"] == 1


def test_glue_a_broken_or_missing_check_never_blocks_a_send(live, static_template):
    _queue("a@example-shop-1.test", "a")
    _queue("b@example-shop-2.test", "b")

    with mock.patch.object(communicator_settings, "COMMUNICATOR_PRESEND_CHECK", "tests.test_glue.explode"):
        first = _live_run()
    assert first["sent"] == 2

    _queue("c@example-shop-3.test", "c")
    with mock.patch.object(communicator_settings, "COMMUNICATOR_PRESEND_CHECK", "no.such.module.check"):
        assert _live_run()["sent"] == 1


def test_glue_smtp_recipient_refusal_in_live_sends_message_bounced(
    live, static_template, django_capture_on_commit_callbacks
):
    message = _queue("gone@example-shop-1.test")
    heard = []
    message_bounced.connect(lambda sender, **kw: heard.append(kw), weak=False, dispatch_uid="glue-smtp")
    refusal = smtplib.SMTPRecipientsRefused({"gone@example-shop-1.test": (550, b"no such user")})
    try:
        with (
            django_capture_on_commit_callbacks(execute=True),
            mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=refusal),
        ):
            _live_run()
    finally:
        message_bounced.disconnect(dispatch_uid="glue-smtp")

    assert [(h["message"].pk, h["hard"]) for h in heard] == [(message.pk, True)]


def test_glue_soft_failure_does_not_send_message_bounced(live, static_template, django_capture_on_commit_callbacks):
    _queue("slow@example-shop-1.test")
    heard = []
    message_bounced.connect(lambda sender, **kw: heard.append(kw), weak=False, dispatch_uid="glue-soft")
    try:
        with (
            django_capture_on_commit_callbacks(execute=True),
            mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=smtplib.SMTPServerDisconnected("x")),
        ):
            _live_run()
    finally:
        message_bounced.disconnect(dispatch_uid="glue-soft")

    assert heard == [] and delivery_service.DEFERRED
