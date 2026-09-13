# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Send policy decisions: business day (channel country holidays), hour windows, daily cap, spread.

Every `at` is an aware datetime; it is read in the channel timezone.
"""

import math
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays

from django_communicator import settings as communicator_settings
from django_communicator.models import Channel, SendPolicy
from django_communicator.services import counter_service

SLOT_SEARCH_DAYS = 366


def load_policy(channel: Channel) -> SendPolicy | None:
    """The channel policy with its windows; raises `NotImplementedError` for a country unknown to `holidays`."""
    policy = SendPolicy.objects.select_related("channel").prefetch_related("windows").filter(channel=channel).first()
    if policy is not None:
        holidays.country_holidays(channel.country)
    return policy


def _local(policy: SendPolicy, at: datetime) -> datetime:
    return at.astimezone(ZoneInfo(policy.channel.timezone))


def _windows(policy: SendPolicy) -> list:
    return sorted(policy.windows.all(), key=lambda window: (window.start_time, window.order))


def is_business_day(policy: SendPolicy, day: date) -> bool:
    if not policy.business_days_only:
        return True
    return day.weekday() < 5 and day not in holidays.country_holidays(policy.channel.country, years=day.year)


def is_open(policy: SendPolicy, at: datetime) -> bool:
    local = _local(policy, at)
    in_window = any(w.start_time <= local.time() < w.end_time for w in _windows(policy))
    return in_window and is_business_day(policy, local.date())


def next_slot(policy: SendPolicy, after: datetime) -> datetime | None:
    """`after` itself when open, else the next window start on a business day; None without windows."""
    local = _local(policy, after)
    if is_open(policy, local):
        return local
    for offset in range(SLOT_SEARCH_DAYS):
        day = local.date() + timedelta(days=offset)
        if not is_business_day(policy, day):
            continue
        starts = [datetime.combine(day, w.start_time, tzinfo=local.tzinfo) for w in _windows(policy)]
        upcoming = [start for start in starts if start > local]
        if upcoming:
            return upcoming[0]
    return None


def remaining_today(policy: SendPolicy, at: datetime) -> int:
    sent = counter_service.sent_on(policy.channel, _local(policy, at).date())
    return max(policy.daily_cap - sent, 0)


def runs_left(policy: SendPolicy, at: datetime) -> int:
    """Beat runs left in the window containing `at`, this one included (1 outside any window)."""
    local = _local(policy, at)
    window = next((w for w in _windows(policy) if w.start_time <= local.time() < w.end_time), None)
    if window is None:
        return 1
    end = datetime.combine(local.date(), window.end_time, tzinfo=local.tzinfo)
    interval = timedelta(minutes=communicator_settings.COMMUNICATOR_SEND_INTERVAL_MIN)
    return max(math.ceil((end - local) / interval), 1)


def should_send_now(policy: SendPolicy, at: datetime, remaining: int, rng: random.Random | None = None) -> bool:
    """False without cap left; with `spread`, true with probability remaining / runs left in the window."""
    if remaining <= 0 or not is_open(policy, at):
        return False
    if not policy.spread:
        return True
    draw = (rng or random).random()  # noqa: S311 — pacing, not security
    return draw < remaining / runs_left(policy, at)
