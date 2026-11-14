# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Sending from mailboxes: From, CC and login per mailbox, unusable logins, per-mailbox cap with warm-up, pause."""

import smtplib
from datetime import date, timedelta
from unittest import mock

import pytest
import redis
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import get_connection

from django_communicator.enums import MailboxState, MessageStatus
from django_communicator.models import Mailbox, Message
from django_communicator.services import counter_service, mail_builder, mailbox_service, send_service
from django_communicator.services.communicate_service import RecipientData, communicate
from tests.conftest import CHANNEL_IDX, MONDAY_10, api_url
from tests.factories import MAILBOX_SECRET, make_mailbox

NOTIFY = "django_notifications.services.notify_service.notify"
SEND = "django.core.mail.EmailMultiAlternatives.send"
DAY = MONDAY_10.date()


def staff(username: str):
    return get_user_model().objects.create_user(username=username, is_staff=True)


def approved(email: str = "jan@example-shop-1.test", *, user_id: int | None = None, ref: str = "ms:1") -> Message:
    recipient = RecipientData(email=email, first_name="Jan", language="pl")
    return communicate(
        channel_idx=CHANNEL_IDX,
        template_key="followup",
        recipient=recipient,
        context={},
        subject_ref=ref,
        requires_review=False,
        mailbox_user_id=user_id,
    )


def run() -> dict:
    return dict(send_service.run_send_due())


def status(message: Message) -> str:
    message.refresh_from_db()
    return message.status


@pytest.fixture
def two_users(channel):
    anna, piotr = staff("anna"), staff("piotr")
    first = make_mailbox(channel, user=anna, display_name="Anna Nowak", cc_address="crm@mail.example.test")
    second = make_mailbox(channel, "piotr@mail.example.test", user=piotr, display_name="Piotr")
    return (anna, first), (piotr, second)


# --- build ---


def test_each_user_mail_leaves_from_that_users_mailbox(policy, sandbox, static_template, two_users):
    (anna, _), (piotr, _) = two_users
    approved(user_id=anna.pk)
    approved("ewa@example-shop-2.test", user_id=piotr.pk, ref="ms:2")

    assert run()["sent"] == 2

    senders = {sent.extra_headers["X-Original-To"]: sent.from_email for sent in mail.outbox}
    assert senders == {
        "jan@example-shop-1.test": "Anna Nowak <anna@mail.example.test>",
        "ewa@example-shop-2.test": "Piotr <piotr@mail.example.test>",
    }
    assert mail.outbox[0].extra_headers["Message-ID"].endswith("@mail.example.test>")


def test_build_uses_the_mailbox_login_and_cc(channel, static_template, two_users):
    (anna, _), _ = two_users
    message = approved(user_id=anna.pk)
    target = "django_communicator.services.mailbox_service.get_connection"

    with mock.patch(target, wraps=get_connection) as connect:
        built = mail_builder.build(Message.objects.get(pk=message.pk))

    kwargs = connect.call_args.kwargs
    assert (kwargs["host"], kwargs["username"], kwargs["password"], kwargs["use_tls"]) == (
        "smtp.mail.example.test",
        "anna@mail.example.test",
        MAILBOX_SECRET,
        True,
    )
    assert built.cc == ["crm@mail.example.test"] and "X-Original-Cc" not in built.extra_headers


def test_sandbox_keeps_the_cc_in_a_header_only(policy, sandbox, static_template, two_users):
    (anna, _), _ = two_users
    approved(user_id=anna.pk)

    run()

    sent = mail.outbox[0]
    assert (sent.cc, sent.recipients()) == ([], ["sandbox@mail.example.test"])
    assert sent.extra_headers["X-Original-Cc"] == "crm@mail.example.test"


def test_channel_without_mailbox_sends_through_the_channel_entry(policy, sandbox, static_template):
    approved()

    assert run()["sent"] == 1
    assert (mail.outbox[0].from_email, mail.outbox[0].cc) == ("outreach@mail.example.test", [])


# --- unusable login, pause ---


def test_unusable_login_holds_one_message_and_notifies_once(policy, sandbox, static_template, two_users):
    (anna, first), (piotr, _) = two_users
    Mailbox.objects.filter(pk=first.pk).update(smtp_password="")
    held, other = approved(user_id=anna.pk), approved("ewa@example-shop-2.test", user_id=piotr.pk, ref="ms:2")

    with mock.patch(NOTIFY) as notify:
        first_run, second_run = run(), run()

    first.refresh_from_db()
    assert (first_run["sent"], first_run["deferred"], second_run["sent"]) == (1, 1, 0)
    assert (status(held), status(other)) == (MessageStatus.APPROVED, MessageStatus.SENT)
    assert mailbox_service.state(first) == MailboxState.NEEDS_LOGIN
    assert notify.call_count == 1 and notify.call_args.kwargs["severity"] == "high"
    assert counter_service.mailbox_sent_on(first.pk, DAY) == 0


def test_refused_smtp_login_puts_the_message_back_until_a_new_password(
    policy, sandbox, static_template, two_users, admin_api
):
    (anna, first), _ = two_users
    message = approved(user_id=anna.pk)

    with mock.patch(SEND, side_effect=smtplib.SMTPAuthenticationError(535, b"bad login")), mock.patch(NOTIFY) as notify:
        assert run()["deferred"] == 1
    with mock.patch(SEND) as send:
        assert run()["sent"] == 0
    held = Message.objects.get(pk=message.pk)
    admin_api.patch(api_url(f"mailboxes/{first.pk}/"), {"smtp_password": "a-new-one"}, format="json")

    assert (held.status, held.send_attempts, send.call_count, notify.call_count) == ("approved", 0, 0, 1)
    assert run()["sent"] == 1 and status(message) == MessageStatus.SENT


def test_paused_mailbox_holds_its_mail_while_the_other_sends(policy, sandbox, static_template, two_users, admin_api):
    (anna, first), (piotr, _) = two_users
    held, other = approved(user_id=anna.pk), approved("ewa@example-shop-2.test", user_id=piotr.pk, ref="ms:2")
    pause_url = api_url(f"mailboxes/{first.pk}/pause/")
    assert admin_api.post(pause_url, {"reason": "list import"}, format="json").json()["state"] == "paused"

    paused_run = run()
    admin_api.post(api_url(f"mailboxes/{first.pk}/resume/"), {"reason": "checked"}, format="json")
    resumed_run = run()

    assert (paused_run["sent"], paused_run["deferred"], resumed_run["sent"]) == (1, 1, 1)
    assert status(held) == status(other) == MessageStatus.SENT
    assert [sent.from_email for sent in mail.outbox] == [
        "Piotr <piotr@mail.example.test>",
        "Anna Nowak <anna@mail.example.test>",
    ]


def test_mailbox_paused_after_the_build_is_not_sent(policy, sandbox, static_template, two_users):
    (anna, first), _ = two_users
    message = approved(user_id=anna.pk)
    build = mail_builder.build

    def build_then_pause(current):
        built = build(current)
        Mailbox.objects.filter(pk=first.pk).update(paused_at=MONDAY_10)
        return built

    with mock.patch.object(mail_builder, "build", side_effect=build_then_pause):
        assert run()["deferred"] == 1

    assert (status(message), len(mail.outbox)) == (MessageStatus.APPROVED, 0)


# --- caps and warm-up ---


def test_mailbox_cap_defers_the_rest_and_counts_per_mailbox(policy, sandbox, static_template, two_users):
    (anna, first), _ = two_users
    Mailbox.objects.filter(pk=first.pk).update(daily_cap=1)
    approved(user_id=anna.pk)
    approved("ewa@example-shop-2.test", user_id=anna.pk, ref="ms:2")

    counts = run()

    assert (counts["sent"], counts["deferred"]) == (1, 1)
    assert counter_service.mailbox_sent_on(first.pk, DAY) == 1
    assert counter_service.sent_on(policy.channel, DAY) == 1  # the deferred one gave its channel place back


def test_failed_delivery_releases_the_mailbox_place(policy, sandbox, static_template, two_users):
    (anna, first), _ = two_users
    approved(user_id=anna.pk)

    with mock.patch(SEND, side_effect=smtplib.SMTPServerDisconnected("gone")):
        run()

    assert counter_service.mailbox_sent_on(first.pk, DAY) == 0


def test_soft_bounce_retry_takes_no_mailbox_place(policy, sandbox, static_template, two_users):
    (anna, first), _ = two_users
    Mailbox.objects.filter(pk=first.pk).update(daily_cap=1)
    message = approved(user_id=anna.pk)
    counter_service.reserve_mailbox(first.pk, DAY, 1)  # the cap is used up
    Message.objects.filter(pk=message.pk).update(
        status=MessageStatus.SCHEDULED, bounce_retry_at=MONDAY_10, message_id="<m@mail.example.test>"
    )

    assert run()["sent"] == 1
    assert counter_service.mailbox_sent_on(first.pk, DAY) == 1


@pytest.mark.parametrize(
    ("day", "cap"),
    [
        (date(2026, 9, 10), 5),  # before the start: the start cap
        (date(2026, 9, 14), 5),  # day 0
        (date(2026, 9, 17), 20),  # day 3: 5 + 3 × 5
        (date(2026, 9, 30), 40),  # grown past the ceiling
    ],
)
def test_warmup_arithmetic(channel, day, cap):
    mailbox = make_mailbox(
        channel, daily_cap=40, warmup_start_cap=5, warmup_step=5, warmup_started_on=date(2026, 9, 14)
    )
    assert mailbox_service.effective_cap(mailbox, day) == cap


def test_cap_without_warmup_and_without_cap(channel):
    assert mailbox_service.effective_cap(make_mailbox(channel, daily_cap=7), DAY) == 7
    assert mailbox_service.effective_cap(make_mailbox(channel, "b@x.test"), DAY + timedelta(days=9)) is None


def test_dev_reset_counters_clears_mailbox_counters(policy, two_users, admin_api):
    (_, first), _ = two_users
    counter_service.reserve_mailbox(first.pk, DAY, None)

    admin_api.post(api_url("test/reset-counters/"), {"days": [DAY.isoformat()]}, format="json")

    assert counter_service.mailbox_sent_on(first.pk, DAY) == 0


def test_redis_failure_on_the_mailbox_place_gives_the_channel_place_back(policy, sandbox, static_template, two_users):
    (anna, first), _ = two_users
    approved(user_id=anna.pk)

    with mock.patch.object(counter_service, "reserve_mailbox", side_effect=redis.ConnectionError("down")):
        send_service.run_send_due()

    assert counter_service.sent_on(policy.channel, DAY) == 0 and len(mail.outbox) == 0
