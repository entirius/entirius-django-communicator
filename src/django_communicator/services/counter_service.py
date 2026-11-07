# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Daily delivery counters in Redis (per channel, and per From mailbox for sender routes): a key per channel day, expiring after 48 h, reserved before SMTP."""

from datetime import date
from functools import cache

import redis

from django_communicator import settings as communicator_settings
from django_communicator.models import Channel

EXPIRE_S = 48 * 3600


@cache
def _client() -> redis.Redis:
    return redis.Redis.from_url(communicator_settings.COMMUNICATOR_REDIS_URL)


def _key(channel: Channel, day: date) -> str:
    return communicator_settings.COMMUNICATOR_SENT_COUNTER_KEY.format(channel_idx=channel.idx, day=day.isoformat())


def sent_on(channel: Channel, day: date) -> int:
    """Deliveries counted for `day` — a date in the channel timezone."""
    return int(_client().get(_key(channel, day)) or 0)


def reserve(channel: Channel, day: date, cap: int) -> bool:
    """Atomic cap check: `INCR` first, then `DECR` and False when the count went over `cap`."""
    key = _key(channel, day)
    pipeline = _client().pipeline()
    pipeline.incr(key)
    pipeline.expire(key, EXPIRE_S)
    if int(pipeline.execute()[0]) <= cap:
        return True
    _client().decr(key)
    return False


def release(channel: Channel, day: date) -> None:
    """Give back a reservation that did not end in a delivery."""
    _client().decr(_key(channel, day))


def reset(channel: Channel, day: date) -> None:
    _client().delete(_key(channel, day))


def _mailbox_key(mailbox: str, day: date) -> str:
    return communicator_settings.COMMUNICATOR_MAILBOX_COUNTER_KEY.format(
        mailbox=mailbox.strip().lower(), day=day.isoformat()
    )


def mailbox_sent_on(mailbox: str, day: date) -> int:
    return int(_client().get(_mailbox_key(mailbox, day)) or 0)


def reserve_mailbox(mailbox: str, day: date, cap: int) -> bool:
    """Atomic per-mailbox cap check, like `reserve`."""
    key = _mailbox_key(mailbox, day)
    pipeline = _client().pipeline()
    pipeline.incr(key)
    pipeline.expire(key, EXPIRE_S)
    if int(pipeline.execute()[0]) <= cap:
        return True
    _client().decr(key)
    return False


def release_mailbox(mailbox: str, day: date) -> None:
    _client().decr(_mailbox_key(mailbox, day))


def reset_mailbox(mailbox: str, day: date) -> None:
    _client().delete(_mailbox_key(mailbox, day))
