# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Daily delivery counter per channel in Redis: `INCR` on a key per channel day, expiring after 48 h."""

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


def increment(channel: Channel, day: date) -> int:
    key = _key(channel, day)
    pipeline = _client().pipeline()
    pipeline.incr(key)
    pipeline.expire(key, EXPIRE_S)
    return int(pipeline.execute()[0])
