# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import random
from datetime import datetime, timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError

from django_communicator.enums import ChannelMode
from django_communicator.models import Channel
from django_communicator.services import channel_service, clock_service, counter_service, policy_service, send_service
from tests.conftest import MONDAY_10, WARSAW, approved_message
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


def test_spread_sends_with_probability_of_remaining_over_runs_left(policy):
    policy.spread = True
    last_run = _at(2026, 9, 14, 16, 56)

    assert policy_service.runs_left(policy, _at(2026, 9, 14, 8, 0)) == 108
    assert policy_service.should_send_now(policy, last_run, remaining=1, rng=random.Random(7))
    assert not policy_service.should_send_now(policy, MONDAY_10, remaining=0)
    draws = [policy_service.should_send_now(policy, MONDAY_10, 1, random.Random(seed)) for seed in range(200)]
    assert 0 < sum(draws) < 20


def test_unknown_country_raises_at_policy_load(policy):
    Channel.objects.filter(pk=policy.channel.pk).update(country="XX")

    with pytest.raises(NotImplementedError):
        policy_service.load_policy(Channel.objects.get(pk=policy.channel.pk))


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
