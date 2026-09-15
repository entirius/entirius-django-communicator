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
    if apps.is_installed("django_notifications"):
        send(channel.idx, subject_ref=subject_ref, severity=severity, title=title)


def send(channel_idx: str, *, subject_ref: str, severity: str, title: str) -> None:
    """`notify()` on the notifications channel of the same idx; an undeliverable alert is an ERROR, never raised."""
    from django_notifications.services.notify_service import notify

    try:
        notify(
            channel_idx=channel_idx,
            recipient_role="sales_admin",
            severity=severity,
            subject_ref=subject_ref,
            title=title,
        )
    except Exception:  # noqa: BLE001 — a notification failure must not stop the beat, the inbound flow or a draft
        logger.error(
            "communicator alert lost: communicator channel %s -> notifications channel %s (missing or failing): "
            "%s [%s] %s",
            channel_idx,
            channel_idx,
            severity,
            subject_ref,
            title,
            exc_info=True,
        )
