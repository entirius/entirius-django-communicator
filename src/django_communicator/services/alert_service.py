# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Channel alerts for the sales admin, at most once per channel, kind and channel day."""

import logging
from datetime import date

from django.apps import apps
from django.core.cache import cache

from django_communicator.models import Channel

logger = logging.getLogger(__name__)

ONCE_TIMEOUT_S = 48 * 3600


def notify_once(channel: Channel, *, kind: str, severity: str, title: str, day: date) -> bool:
    """True when this call notified. Soft dependency: without django_notifications only a warning is logged."""
    if not cache.add(f"communicator:alert:{kind}:{channel.idx}:{day.isoformat()}", 1, timeout=ONCE_TIMEOUT_S):
        return False
    logger.warning("communicator channel %s: %s", channel.idx, title)
    if not apps.is_installed("django_notifications"):
        return True
    from django_notifications.services.notify_service import notify

    try:
        ref = f"communicator.Channel:{channel.idx}"
        notify(channel_idx=channel.idx, recipient_role="sales_admin", severity=severity, subject_ref=ref, title=title)
    except Exception:  # noqa: BLE001 — a notification failure must not stop the beat
        logger.warning("communicator could not notify about channel %s", channel.idx, exc_info=True)
    return True
