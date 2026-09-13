# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from datetime import datetime, timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError

from django_communicator.enums import ChannelMode
from django_communicator.models import Channel
from django_communicator.services import channel_service, clock_service, counter_service, policy_service, send_service
from django_communicator.services.communicate_service import RecipientData, communicate
from tests.conftest import CHANNEL_IDX, MONDAY_10, WARSAW, api_url, approved_message
from tests.factories import ChannelFactory


def _at(*args) -> datetime:
    return datetime(*args, tzinfo=WARSAW)


@pytest.mark.parametrize(
    ("at", "slot"),
    [
        (_at(2026, 9, 14, 10, 0), _at(2026, 9, 14, 10, 0)),  # open: now
        (_at(2026, 9, 14, 7, 0), _at(2026, 9, 14, 8, 0)),  # before the window
        (_at(2026, 9, 14, 17, 0), _at(2026, 9, 15, 8, 0)),  # window end is exclusive
        (_at(2026, 9, 19, 10, 0), _at(2026, 9, 21, 8, 0)),  # Saturday
        (_at(2026, 11, 11, 10, 0), _at(2026, 11, 12, 8, 0)),  # PL Independence Day
        (_at(2026, 12, 23, 17, 30), _at(2026, 12, 28, 8, 0)),  # Christmas Eve, 25th, 26th, Sunday
    ],
)
def test_C16_outside_window_weekend_holiday_next_slot(policy, at, slot):
    assert policy_service.next_slot(policy, at) == slot
    assert policy_service.is_open(policy, at) == (at == slot)


def test_C16_closed_channel_defers_without_sending(policy, sandbox, static_template):
    clock_service.set_override(policy.channel, _at(2026, 9, 19, 10, 0))
    message = approved_message()

    counts = send_service.run_send_due()

    message.refresh_from_db()
    assert (counts["deferred"], message.status, len(mail.outbox)) == (1, "approved", 0)
    assert send_service.next_slot_for(message, policy) == _at(2026, 9, 21, 8, 0)


def test_C17_daily_cap_counter_per_channel_day_resets_at_channel_midnight(policy, sandbox, static_template):
    policy.daily_cap = 2
    policy.save()
    for n in range(3):
        approved_message(subject_ref=f"cap:{n}")

    counts = send_service.run_send_due()

    other = ChannelFactory(idx="other-channel", label="Other")
    before_midnight, after_midnight = _at(2026, 9, 14, 23, 59), _at(2026, 9, 15, 0, 1)
    assert (counts["sent"], counts["deferred"], len(mail.outbox)) == (2, 1, 2)
    assert policy_service.remaining_today(policy, before_midnight) == 0
    assert policy_service.remaining_today(policy, after_midnight) == 2
    assert counter_service.sent_on(other, MONDAY_10.date()) == 0


def test_C17_spread_does_not_front_load_backlog(policy, sandbox, static_template):
    policy.spread = True
    policy.save()
    for n in range(5):
        approved_message(subject_ref=f"spread:{n}")

    first = send_service.run_send_due()
    clock_service.set_override(policy.channel, _at(2026, 9, 14, 16, 56))
    last = send_service.run_send_due()

    assert policy_service.runs_today(policy, _at(2026, 9, 14, 8, 0)) == 108
    assert (first["sent"], first["deferred"]) == (2, 3)  # floor(10 * 25 elapsed runs / 108)
    assert (last["sent"], last["deferred"]) == (3, 0)  # the last run of the day may use the rest


def test_C17_spread_cap_10_over_108_runs_spreads_evenly(policy, sandbox, static_template):
    policy.spread = True
    policy.save()
    for n in range(12):
        approved_message(subject_ref=f"spread-day:{n}")

    sent_per_run = []
    for run in range(108):
        clock_service.set_override(policy.channel, _at(2026, 9, 14, 8, 0) + timedelta(minutes=5 * run))
        sent_per_run.append(send_service.run_send_due()["sent"])

    sending_runs = [run for run, sent in enumerate(sent_per_run) if sent]
    gaps = {later - earlier for earlier, later in zip(sending_runs, sending_runs[1:], strict=False)}
    assert (max(sent_per_run), sum(sent_per_run), len(mail.outbox)) == (1, 10, 10)
    assert (sending_runs[0], sending_runs[-1], gaps) == (10, 107, {10, 11})


def test_unknown_country_is_409_not_500(policy, static_template, admin_api):
    recipient = RecipientData(email="jan@example-shop-1.test", first_name="Jan", language="pl")
    draft = communicate(
        channel_idx=CHANNEL_IDX, template_key="followup", recipient=recipient, context={}, subject_ref="c409"
    )
    Channel.objects.filter(pk=policy.channel.pk).update(country="XX")

    responses = [
        admin_api.get(api_url("policy/")),
        admin_api.get(api_url("messages/")),
        admin_api.post(api_url(f"review/{draft.pk}/accept/")),
    ]

    draft.refresh_from_db()
    assert [response.status_code for response in responses] == [409, 409, 409]
    assert responses[0].json()["error"] == "CHANNEL_CONFIG_INVALID" and draft.status == "review_required"
    with pytest.raises(policy_service.ChannelConfigError):
        policy_service.load_policy(Channel.objects.get(pk=policy.channel.pk))


def test_invalid_timezone_rejected_on_save(channel):
    channel.timezone = "Mars/Olympus_Mons"
    with pytest.raises(ValidationError):
        channel.save()

    channel.timezone, channel.country = "Europe/Warsaw", "XX"
    with pytest.raises(ValidationError):
        channel.clean()
    channel.refresh_from_db()
    assert (channel.timezone, channel.country) == ("Europe/Warsaw", "PL")


def test_C30_sandbox_without_mailbox_refused(channel):
    with pytest.raises(ValidationError):
        channel_service.set_mode(channel, mode=ChannelMode.SANDBOX, sandbox_mailbox="")
    with pytest.raises(ValidationError):
        channel_service.set_mode(channel, mode=ChannelMode.LIVE)

    channel.refresh_from_db()
    assert (channel.mode, channel.live_enabled) == (ChannelMode.DRY_RUN, False)
    assert channel_service.set_mode(channel, mode=ChannelMode.LIVE, live_enabled=True).mode == ChannelMode.LIVE


def test_clock_override_only_in_development(channel, settings):
    clock_service.set_override(channel, datetime(2026, 9, 14, 10, 0))
    assert clock_service.now_for(channel) == MONDAY_10

    settings.ENVIRONMENT = "production"
    assert abs(clock_service.now_for(channel) - datetime.now(tz=WARSAW)) < timedelta(minutes=1)
    with pytest.raises(RuntimeError):
        clock_service.set_override(channel, None)
