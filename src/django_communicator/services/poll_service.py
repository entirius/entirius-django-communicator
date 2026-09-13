# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""IMAP poll of every active mailbox: read-only EXAMINE, `last_uid` cursor, never deletes or flags mail.

The cursor moves past a UID only once its mail was ingested (or dropped as unmatched) or recorded in
`InboundQuarantine`. Transient failures — a non-OK IMAP status, connection loss, a database outage — stop that
mailbox's run without moving it; the next beat reads the mail again. Credentials and bodies are never logged.
"""

import imaplib
import logging
import re
from collections import Counter

from django.db import DatabaseError, DataError
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import QuarantineReason
from django_communicator.models import Channel, InboundQuarantine, MailboxConfig
from django_communicator.services import alert_service, clock_service, inbound_service

logger = logging.getLogger(__name__)

IMAP_ERRORS = (imaplib.IMAP4.error, OSError)
IMAP_TIMEOUT_S = 30
INGESTED, SKIPPED, QUARANTINED = "ingested", "skipped", "quarantined"
_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")


class ImapStatusError(imaplib.IMAP4.error):
    """The server answered NO/BAD (or left out what it had to send) — transient, the cursor stays."""


def poll_all(channel: Channel | None = None) -> Counter:
    """Counts over the active mailboxes (of `channel` when given); each mailbox fails on its own, the last failure
    re-raises after every mailbox ran."""
    counts, failure = Counter({INGESTED: 0, SKIPPED: 0, QUARANTINED: 0}), None
    configs = MailboxConfig.objects.select_related("channel").filter(is_active=True)
    for config in configs.filter(channel=channel) if channel else configs:
        try:
            counts.update(poll_mailbox(config))
        except Exception as error:  # noqa: BLE001 — one broken mailbox must not stop the others
            failure = error
            _alert(config, error)
    if failure is not None:
        raise failure
    return counts


def _alert(config: MailboxConfig, error: Exception) -> None:
    logger.warning("communicator IMAP poll of channel %s failed: %s", config.channel.idx, type(error).__name__)
    day = clock_service.now_for(config.channel).date()
    alert_service.notify_once(config.channel, kind="imap", severity="medium", title="IMAP poll failed", day=day)


def poll_mailbox(config: MailboxConfig) -> Counter:
    counts = Counter()
    with _connect(config) as imap:
        _examine(imap, config)
        for uid in _new_uids(imap, config)[: communicator_settings.COMMUNICATOR_INBOUND_BATCH]:
            counts[_process_uid(imap, config, uid)] += 1
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


def _require_ok(status: str, command: str) -> None:
    if status != "OK":
        raise ImapStatusError(f"IMAP {command} answered {status}")


def _examine(imap: imaplib.IMAP4, config: MailboxConfig) -> None:
    """EXAMINE the folder and read its UIDVALIDITY; a changed one restarts the cursor (dedup skips known mail)."""
    status, _ = imap.select(_quoted(config.folder), readonly=True)
    _require_ok(status, "EXAMINE")
    _, data = imap.response("UIDVALIDITY")
    if not data or not data[0]:
        raise ImapStatusError("EXAMINE sent no UIDVALIDITY")
    validity = int(data[0])
    if validity == config.uid_validity:
        return
    if config.uid_validity is not None:
        logger.warning("communicator mailbox %s: UIDVALIDITY changed, reading from UID 0", config.pk)
        config.last_uid = 0
    config.uid_validity = validity
    MailboxConfig.objects.filter(pk=config.pk).update(uid_validity=validity, last_uid=config.last_uid)


def _new_uids(imap: imaplib.IMAP4, config: MailboxConfig) -> list[int]:
    """UIDs above the cursor, oldest first (`n:*` always matches the highest UID, so it is filtered out)."""
    status, data = imap.uid("SEARCH", None, f"UID {config.last_uid + 1}:*")
    _require_ok(status, "SEARCH")
    uids = sorted(int(uid) for uid in (data[0] or b"").split())
    return [uid for uid in uids if uid > config.last_uid]


def _process_uid(imap: imaplib.IMAP4, config: MailboxConfig, uid: int) -> str:
    """`ingested`, `skipped` (unmatched, or expunged since SEARCH) or `quarantined`; transient errors raise."""
    size = _fetch_size(imap, uid)
    if size is not None and size > communicator_settings.COMMUNICATOR_INBOUND_MAX_BYTES:
        return _quarantine(config, uid, QuarantineReason.OVERSIZED, size)
    raw = _fetch_raw(imap, uid) if size is not None else None
    outcome = _ingest(config, uid, raw) if raw else SKIPPED
    if outcome != QUARANTINED:
        _advance(config, uid)
    return outcome


def _fetch_size(imap: imaplib.IMAP4, uid: int) -> int | None:
    """None when the mail is gone (FETCH OK without data)."""
    status, data = imap.uid("FETCH", str(uid), "(RFC822.SIZE)")
    _require_ok(status, "FETCH")
    found = _SIZE.search(b" ".join(part for part in data if isinstance(part, bytes)))
    return int(found.group(1)) if found else None


def _fetch_raw(imap: imaplib.IMAP4, uid: int) -> bytes | None:
    status, data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
    _require_ok(status, "FETCH")
    return next((part[1] for part in data if isinstance(part, tuple)), None)


def _ingest(config: MailboxConfig, uid: int, raw: bytes) -> str:
    """Database outages re-raise (transient); mail the database or the parser can never take is quarantined."""
    try:
        reply = inbound_service.ingest(config.channel, raw)
    except DataError:
        return _quarantine(config, uid, QuarantineReason.DATA_ERROR, len(raw))
    except DatabaseError:
        raise
    except Exception as error:  # noqa: BLE001 — a mail the parser cannot read must not block the mailbox
        logger.warning("communicator mailbox %s: mail uid %s unreadable: %s", config.pk, uid, type(error).__name__)
        return _quarantine(config, uid, QuarantineReason.UNPARSEABLE, len(raw))
    return INGESTED if reply is not None else SKIPPED


def _quarantine(config: MailboxConfig, uid: int, reason: str, size: int) -> str:
    InboundQuarantine.objects.create(mailbox=config, uid=uid, reason=reason, size=size, received_at=timezone.now())
    logger.warning("communicator mailbox %s: mail uid %s quarantined (%s)", config.pk, uid, reason)
    _advance(config, uid)
    return QUARANTINED


def _advance(config: MailboxConfig, uid: int) -> None:
    config.last_uid = uid
    MailboxConfig.objects.filter(pk=config.pk).update(last_uid=uid)
