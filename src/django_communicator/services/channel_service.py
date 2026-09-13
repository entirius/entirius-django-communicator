# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.db import transaction

from django_communicator.enums import ChannelMode
from django_communicator.models import Channel

CHANNEL_CONFIG_FIELDS = ("mode", "sandbox_mailbox", "live_enabled")


def get_channel(idx: str) -> Channel:
    """Raises `Channel.DoesNotExist` for an unknown idx."""
    return Channel.objects.get(idx=idx)


@transaction.atomic
def set_mode(channel: Channel, **changes) -> Channel:
    """Apply `mode`, `sandbox_mailbox`, `live_enabled`; raises `ValidationError` on an unsafe combination (C-30)."""
    locked = Channel.objects.select_for_update().get(pk=channel.pk)
    for name in CHANNEL_CONFIG_FIELDS:
        if changes.get(name) is not None:
            setattr(locked, name, changes[name])
    locked.clean()
    locked.save(update_fields=[*CHANNEL_CONFIG_FIELDS, "modified_at"])
    for name in CHANNEL_CONFIG_FIELDS:
        setattr(channel, name, getattr(locked, name))
    return channel


def live_allowed(channel: Channel) -> bool:
    """The live double gate, with no setting to drop it: production, the channel flag and live mode."""
    in_production = getattr(settings, "ENVIRONMENT", "") == "production"
    return in_production and channel.live_enabled and channel.mode == ChannelMode.LIVE
