# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Send policy decisions: business day (channel country holidays), hour windows, daily cap, spread.

Every `at` is an aware datetime; it is read in the channel timezone.
"""

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays
from django.core.exceptions import ValidationError

from django_communicator import settings as communicator_settings
from django_communicator.models import Channel, SendPolicy
from django_communicator.services import counter_service

SLOT_SEARCH_DAYS = 366


class ChannelConfigError(Exception):
    """The stored channel country or timezone cannot drive a send policy."""


def load_policy(channel: Channel) -> SendPolicy | None:
    """The channel policy with its windows; raises `ChannelConfigError` for a bad stored country or timezone."""
    policy = SendPolicy.objects.select_related("channel").prefetch_related("windows").filter(channel=channel).first()
    if policy is None:
        return None
    try:
        policy.channel.validate_locale()
    except ValidationError as error:
        raise ChannelConfigError("; ".join(error.messages)) from None
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


def channel_day(policy: SendPolicy, at: datetime) -> date:
    return _local(policy, at).date()


def remaining_today(policy: SendPolicy, at: datetime) -> int:
    sent = counter_service.sent_on(policy.channel, channel_day(policy, at))
    return max(policy.daily_cap - sent, 0)


def _interval() -> timedelta:
    return timedelta(minutes=communicator_settings.COMMUNICATOR_SEND_INTERVAL_MIN)


def _window_runs(window, day: date) -> int:
    span = datetime.combine(day, window.end_time) - datetime.combine(day, window.start_time)
    return max(math.ceil(span / _interval()), 0)


def _elapsed_runs(window, local: datetime) -> int:
    """Runs of `window` started by `local`, the one at `local` included."""
    if local.time() < window.start_time:
        return 0
    start = datetime.combine(local.date(), window.start_time, tzinfo=local.tzinfo)
    return min((local - start) // _interval() + 1, _window_runs(window, local.date()))


def runs_today(policy: SendPolicy, at: datetime) -> int:
    """Beat runs across all of today's windows."""
    day = _local(policy, at).date()
    return sum(_window_runs(window, day) for window in _windows(policy))


def spread_allowed(policy: SendPolicy, at: datetime) -> int:
    """Running quota: the daily cap in proportion to today's runs elapsed so far, this one included."""
    total = runs_today(policy, at)
    if total == 0:
        return 0
    elapsed = sum(_elapsed_runs(window, _local(policy, at)) for window in _windows(policy))
    return policy.daily_cap * elapsed // total


def run_budget(policy: SendPolicy, at: datetime) -> int:
    """Deliveries this run may make: 0 when closed; with `spread`, the running quota minus today's deliveries."""
    if not is_open(policy, at):
        return 0
    if not policy.spread:
        return remaining_today(policy, at)
    sent = counter_service.sent_on(policy.channel, channel_day(policy, at))
    return max(spread_allowed(policy, at) - sent, 0)
