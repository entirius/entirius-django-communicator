# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Sending health per mailbox: counts, the auto-pause (live only, above the sample), manual pause / resume, log."""

import logging
from datetime import timedelta
from unittest import mock

import pytest

from django_communicator.enums import (
    ChannelMode,
    Direction,
    MessageStatus,
    ReplyKind,
    SuppressionKind,
    SuppressionSource,
)
from django_communicator.models import (
    Channel,
    HealthPolicy,
    Mailbox,
    MailboxPauseLog,
    Message,
    Reply,
    Suppression,
    Thread,
)
from django_communicator.services import counter_service, health_service, send_service
from tests.conftest import MONDAY_10, api_url, approved_message
from tests.factories import MAILBOX_SECRET, make_mailbox

NOTIFY = "django_notifications.services.notify_service.notify"


def sent_mail(mailbox: Mailbox | None, n: int, *, channel: Channel | None = None, start: int = 0) -> list[Message]:
    """`n` sent mails on threads of `mailbox` (or of no mailbox), each to its own recipient, one hour ago."""
    channel = channel or mailbox.channel
    messages = []
    for i in range(start, start + n):
        thread = Thread.objects.create(
            channel=channel, subject_ref=f"h:{i}", recipient_email=f"r{i}@example-shop.test", mailbox=mailbox
        )
        messages.append(
            Message.objects.create(
                thread=thread,
                status=MessageStatus.SENT,
                direction=Direction.OUT,
                sent_at=MONDAY_10 - timedelta(hours=1),
            )
        )
    return messages


def reply(message: Message, kind: str, *, hours_ago: int = 1) -> Reply:
    thread = message.thread
    return Reply.objects.create(
        channel=thread.channel,
        thread=thread,
        message=message,
        from_email=thread.recipient_email,
        kind=kind,
        inbound_message_id=f"<{message.pk}-{kind}@example-shop.test>",
        received_at=MONDAY_10 - timedelta(hours=hours_ago),
    )


def live(channel: Channel) -> Channel:
    Channel.objects.filter(pk=channel.pk).update(mode=ChannelMode.LIVE)
    channel.refresh_from_db()
    return channel


@pytest.fixture
def two(channel):
    return make_mailbox(channel), make_mailbox(channel, "piotr@mail.example.test")


def test_counts_attributed_by_thread_mailbox(channel, two):
    first, second = two
    first_mail, second_mail = sent_mail(first, 4), sent_mail(second, 2, start=10)
    sent_mail(None, 1, channel=channel, start=20)
    reply(first_mail[0], ReplyKind.BOUNCE_HARD)
    reply(first_mail[1], ReplyKind.REPLY)
    reply(second_mail[0], ReplyKind.COMPLAINT)
    Suppression.objects.create(
        channel=channel,
        kind=SuppressionKind.EMAIL,
        value=first_mail[2].thread.recipient_email,
        source=SuppressionSource.OPTOUT_CONFIRMED,
    )

    found = health_service.counts(channel, MONDAY_10 - timedelta(days=1))

    assert found[first.pk] == {
        "sent": 4, "hard_bounces": 1, "soft_bounces": 0, "complaints": 0, "optouts": 1, "replies": 1,
    }  # fmt: skip
    assert (found[second.pk]["sent"], found[second.pk]["complaints"], found[None]["sent"]) == (2, 1, 1)


def test_a_delivery_without_smtp_counts_as_sent(channel, two):
    first, _ = two
    delivered = sent_mail(first, 3)
    Message.objects.filter(pk__in=[m.pk for m in delivered[:2]]).update(status=MessageStatus.WOULD_SEND)

    found = health_service.counts(channel, MONDAY_10 - timedelta(days=1))

    assert found[first.pk]["sent"] == 3


def test_health_endpoint_shows_rates_and_todays_cap(policy, two, admin_api):
    first, _ = two
    Mailbox.objects.filter(pk=first.pk).update(daily_cap=30, warmup_start_cap=10, warmup_step=5)
    Mailbox.objects.filter(pk=first.pk).update(warmup_started_on=MONDAY_10.date() - timedelta(days=2))
    reply(sent_mail(first, 4)[0], ReplyKind.BOUNCE_HARD)
    counter_service.reserve_mailbox(first.pk, MONDAY_10.date(), None)

    body = admin_api.get(api_url("health/")).json()

    row = next(row for row in body["mailboxes"] if row["id"] == first.pk)
    assert (row["sent_today"], row["cap_today"], row["state"]) == (1, 20, "active")
    assert row["windows"]["24h"]["sent"] == 4 and row["windows"]["24h"]["hard_bounces_pct"] == 25.0
    assert body["channel"]["windows"]["7d"]["sent"] == 4 and len(body["mailboxes"]) == 2


@pytest.fixture
def strict_policy(channel) -> HealthPolicy:
    return HealthPolicy.objects.create(channel=channel, enabled=True, min_sample=4, max_bounce_pct=10)


def test_auto_pause_on_live_above_the_threshold_only(channel, two, strict_policy):
    first, second = two
    reply(sent_mail(first, 4)[0], ReplyKind.BOUNCE_HARD)  # 25 % > 10 %
    reply(sent_mail(second, 3, start=10)[0], ReplyKind.BOUNCE_HARD)  # below the sample

    with mock.patch(NOTIFY) as notify:
        paused = health_service.evaluate(live(channel), MONDAY_10)
        again = health_service.evaluate(channel, MONDAY_10)

    first.refresh_from_db()
    log = MailboxPauseLog.objects.get()
    assert (paused, again, first.paused_at, first.pause_reason[:13]) == ([first.pk], [], MONDAY_10, "hard bounces ")
    assert (log.mailbox_id, log.action, log.trigger, log.user_id) == (first.pk, "pause", "bounce", None)
    assert log.metrics["hard_bounces_pct"] == 25.0
    assert notify.call_count == 1 and notify.call_args.kwargs["severity"] == "high"


@pytest.mark.parametrize("mode", [ChannelMode.SANDBOX, ChannelMode.DRY_RUN])
def test_auto_pause_never_outside_live(channel, two, strict_policy, mode):
    reply(sent_mail(two[0], 4)[0], ReplyKind.BOUNCE_HARD)
    Channel.objects.filter(pk=channel.pk).update(mode=mode)
    channel.refresh_from_db()
    assert health_service.evaluate(channel, MONDAY_10) == []


def test_auto_pause_off_while_the_policy_is_off(channel, two):
    HealthPolicy.objects.create(channel=channel, enabled=False, min_sample=1)
    reply(sent_mail(two[0], 4)[0], ReplyKind.BOUNCE_HARD)
    assert health_service.evaluate(live(channel), MONDAY_10) == []


def test_resume_counts_only_newer_events(channel, two, strict_policy, admin_api):
    first, _ = two
    reply(sent_mail(first, 4)[0], ReplyKind.BOUNCE_HARD)
    with mock.patch(NOTIFY):
        health_service.evaluate(live(channel), MONDAY_10)

    resumed = admin_api.post(api_url(f"mailboxes/{first.pk}/resume/"), {"reason": "list cleaned"}, format="json")

    assert resumed.status_code == 200 and resumed.json()["state"] == "active"
    assert health_service.evaluate(channel, MONDAY_10) == []
    assert list(MailboxPauseLog.objects.order_by("pk").values_list("action", "trigger")) == [
        ("pause", "bounce"),
        ("resume", "manual"),
    ]


def test_manual_pause_and_resume_need_a_reason_and_the_right_state(channel, two, admin_api):
    first, _ = two
    url = api_url(f"mailboxes/{first.pk}/")

    assert admin_api.post(url + "pause/", {}, format="json").status_code == 400
    assert admin_api.post(url + "resume/", {"reason": "x"}, format="json").status_code == 409
    assert admin_api.post(url + "pause/", {"reason": "holiday"}, format="json").status_code == 200
    assert admin_api.post(url + "pause/", {"reason": "again"}, format="json").status_code == 409

    log = admin_api.get(api_url("health/log/")).json()
    assert log["count"] == 1 and log["results"][0]["user_id"] == admin_api.user.pk
    assert log["results"][0]["reason"] == "holiday"


def test_send_run_evaluates_health_first(policy, sandbox):
    with mock.patch.object(health_service, "evaluate") as evaluate:
        send_service.run_send_due()
    assert evaluate.call_args.args[0] == policy.channel


def test_policy_api_defaults_and_replace(channel, admin_api):
    url = api_url("health/policy/")
    assert admin_api.get(url).json() == {
        "enabled": False, "window_hours": 168, "min_sample": 50,
        "max_bounce_pct": 2.0, "max_optout_pct": 3.0, "max_complaint_pct": 0.1,
    }  # fmt: skip

    saved = admin_api.put(url, {"enabled": True, "min_sample": 20, "max_bounce_pct": "1.5"}, format="json")

    assert saved.json()["min_sample"] == 20 and HealthPolicy.objects.get().max_bounce_pct == pytest.approx(1.5)
    assert admin_api.put(url, {"window_hours": 0}, format="json").status_code == 400


def test_no_password_in_responses_logs_or_notifications(policy, sandbox, static_template, admin_api, caplog):
    mailbox = make_mailbox(policy.channel, is_default=True, imap_host="imap.test", imap_password=MAILBOX_SECRET)
    Mailbox.objects.filter(pk=mailbox.pk).update(smtp_host="")
    caplog.set_level(logging.DEBUG)
    outputs = []

    with mock.patch(NOTIFY) as notify:
        outputs.append(
            admin_api.post(api_url("mailboxes/"), {"address": "b@x.test", "smtp_password": MAILBOX_SECRET}, "json")
        )
        outputs += [admin_api.get(api_url(path)) for path in ("mailboxes/", "health/", "health/log/")]
        outputs.append(admin_api.post(api_url(f"mailboxes/{mailbox.pk}/pause/"), {"reason": "r"}, format="json"))
        Mailbox.objects.filter(pk=mailbox.pk).update(paused_at=None)
        approved_message()
        send_service.run_send_due()

    texts = [response.content.decode() for response in outputs] + [caplog.text, str(notify.call_args_list)]
    assert notify.call_count >= 1 and all(MAILBOX_SECRET not in text for text in texts)
