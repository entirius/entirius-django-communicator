# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""IMAP poll of every active mailbox: read-only EXAMINE, `last_uid` cursor, never deletes or flags mail.

Credentials and bodies are never logged.
"""

import imaplib
import logging
from collections import Counter

from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.models import MailboxConfig
from django_communicator.services import alert_service, clock_service, inbound_service

logger = logging.getLogger(__name__)

IMAP_ERRORS = (imaplib.IMAP4.error, OSError)
IMAP_TIMEOUT_S = 30


def poll_all() -> Counter:
    """Counts `ingested` / `skipped` over all mailboxes; re-raises the last IMAP error after every mailbox ran."""
    counts, failure = Counter(ingested=0, skipped=0), None
    for config in MailboxConfig.objects.select_related("channel").filter(is_active=True):
        try:
            counts.update(poll_mailbox(config))
        except IMAP_ERRORS as error:
            failure = error
            logger.warning("communicator IMAP poll of channel %s failed: %s", config.channel.idx, type(error).__name__)
            day = clock_service.now_for(config.channel).date()
            alert_service.notify_once(config.channel, kind="imap", severity="medium", title="IMAP poll failed", day=day)
    if failure is not None:
        raise failure
    return counts


def poll_mailbox(config: MailboxConfig) -> Counter:
    counts = Counter(ingested=0, skipped=0)
    with _connect(config) as imap:
        status, _ = imap.select(_quoted(config.folder), readonly=True)
        if status != "OK":
            raise imaplib.IMAP4.error(f"cannot select folder of mailbox {config.pk}")
        for uid in _new_uids(imap, config)[: communicator_settings.COMMUNICATOR_INBOUND_BATCH]:
            counts["ingested" if _ingest_uid(imap, config, uid) else "skipped"] += 1
    config.last_polled_at = timezone.now()
    MailboxConfig.objects.filter(pk=config.pk).update(last_polled_at=config.last_polled_at)
    return counts


def _connect(config: MailboxConfig) -> imaplib.IMAP4:
    client = imaplib.IMAP4_SSL if config.imap_use_ssl else imaplib.IMAP4
    imap = client(config.imap_host, config.imap_port, timeout=IMAP_TIMEOUT_S)
    try:
        imap.login(config.imap_user, config.imap_password)
    except IMAP_ERRORS:
        imap.shutdown()
        raise
    return imap


def _quoted(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _new_uids(imap: imaplib.IMAP4, config: MailboxConfig) -> list[int]:
    """UIDs above the cursor, oldest first. A highest UID below the cursor means UIDVALIDITY changed: reset to 0."""
    _, data = imap.uid("SEARCH", None, f"UID {config.last_uid + 1}:*")
    uids = sorted(int(uid) for uid in (data[0] or b"").split())
    if uids and uids[-1] < config.last_uid:
        logger.warning("communicator mailbox %s: UIDs restarted below the cursor, reading from 0", config.pk)
        _advance(config, 0)
        return _new_uids(imap, config)
    return [uid for uid in uids if uid > config.last_uid]


def _ingest_uid(imap: imaplib.IMAP4, config: MailboxConfig, uid: int) -> bool:
    """True when a `Reply` came out; the cursor advances either way, so a crash re-reads at most one mail."""
    _, data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
    raw = next((part[1] for part in data if isinstance(part, tuple)), None)
    try:
        reply = inbound_service.ingest(config.channel, raw) if raw else None
    except Exception:  # noqa: BLE001 — one unreadable mail must not block the mailbox
        logger.exception("communicator mailbox %s: mail uid %s not ingested", config.pk, uid)
        reply = None
    _advance(config, uid)
    return reply is not None


def _advance(config: MailboxConfig, uid: int) -> None:
    config.last_uid = uid
    MailboxConfig.objects.filter(pk=config.pk).update(last_uid=uid)
