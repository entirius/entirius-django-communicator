# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The channel clock: every sending decision reads `now_for(channel)`, never `timezone.now()` directly.

Under `ENVIRONMENT == "development"` a cache override moves one channel's clock (BDD `test/clock/`).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.models import Channel


def _is_development() -> bool:
    return getattr(settings, "ENVIRONMENT", "") == "development"


def _key(channel: Channel) -> str:
    return communicator_settings.COMMUNICATOR_CLOCK_CACHE_KEY.format(channel_idx=channel.idx)


def now_for(channel: Channel) -> datetime:
    """Aware datetime in the channel timezone."""
    override = cache.get(_key(channel)) if _is_development() else None
    now = datetime.fromisoformat(override) if override else timezone.now()
    return now.astimezone(ZoneInfo(channel.timezone))


def set_override(channel: Channel, at: datetime | None) -> None:
    """Freeze the channel clock at `at` (naive = channel timezone); `None` clears. Development only."""
    if not _is_development():
        raise RuntimeError("the channel clock can only be moved in development")
    if at is None:
        cache.delete(_key(channel))
        return
    if timezone.is_naive(at):
        at = at.replace(tzinfo=ZoneInfo(channel.timezone))
    cache.set(_key(channel), at.isoformat(), timeout=None)
