# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Sales-admin notifications: channel alerts at most once per channel, kind and channel day; thread events."""

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
    notify_subject(channel, subject_ref=f"communicator.Channel:{channel.idx}", severity=severity, title=title)
    return True


def notify_subject(channel: Channel, *, subject_ref: str, severity: str, title: str) -> None:
    """Notify the sales admin. Soft dependency: without django_notifications nothing happens."""
    if not apps.is_installed("django_notifications"):
        return
    from django_notifications.services.notify_service import notify

    try:
        notify(
            channel_idx=channel.idx,
            recipient_role="sales_admin",
            severity=severity,
            subject_ref=subject_ref,
            title=title,
        )
    except Exception:  # noqa: BLE001 — a notification failure must not stop the beat or the inbound flow
        logger.warning("communicator could not notify about %s", subject_ref, exc_info=True)
