# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Daily delivery counters in Redis — per channel and per mailbox: a key per day, expiring after 48 h, reserved
before SMTP and given back when the mail was not sent."""

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


def _mailbox_key(mailbox_id: int, day: date) -> str:
    return communicator_settings.COMMUNICATOR_MAILBOX_COUNTER_KEY.format(mailbox_id=mailbox_id, day=day.isoformat())


def _reserve(key: str, cap: int | None) -> bool:
    """Atomic cap check: `INCR` first, then `DECR` and False when the count went over `cap` (None = no cap)."""
    pipeline = _client().pipeline()
    pipeline.incr(key)
    pipeline.expire(key, EXPIRE_S)
    count = int(pipeline.execute()[0])
    if cap is None or count <= cap:
        return True
    _client().decr(key)
    return False


def sent_on(channel: Channel, day: date) -> int:
    """Deliveries counted for `day` — a date in the channel timezone."""
    return int(_client().get(_key(channel, day)) or 0)


def reserve(channel: Channel, day: date, cap: int) -> bool:
    return _reserve(_key(channel, day), cap)


def release(channel: Channel, day: date) -> None:
    """Give back a reservation that did not end in a delivery."""
    _client().decr(_key(channel, day))


def reset(channel: Channel, day: date) -> None:
    """The channel counter and the counter of every mailbox of the channel."""
    mailbox_keys = [_mailbox_key(pk, day) for pk in channel.mailboxes.values_list("pk", flat=True)]
    _client().delete(_key(channel, day), *mailbox_keys)


def mailbox_sent_on(mailbox_id: int, day: date) -> int:
    return int(_client().get(_mailbox_key(mailbox_id, day)) or 0)


def reserve_mailbox(mailbox_id: int, day: date, cap: int | None) -> bool:
    """Counted even without a cap, so `sent_today` of the health report is right."""
    return _reserve(_mailbox_key(mailbox_id, day), cap)


def release_mailbox(mailbox_id: int, day: date) -> None:
    _client().decr(_mailbox_key(mailbox_id, day))
