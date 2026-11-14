# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""IMAP poll of every mailbox with `receive_enabled`: read-only EXAMINE, `last_uid` cursor, never deletes or flags mail.

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
from django_communicator.models import Channel, InboundQuarantine, Mailbox
from django_communicator.services import alert_service, clock_service, inbound_service, mailbox_service

logger = logging.getLogger(__name__)

INGESTED, SKIPPED, QUARANTINED = "ingested", "skipped", "quarantined"
_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")


class ImapStatusError(imaplib.IMAP4.error):
    """The server answered NO/BAD (or left out what it had to send) — transient, the cursor stays."""


def poll_all(channel: Channel | None = None) -> Counter:
    """Counts over the receiving mailboxes (of `channel` when given); each mailbox fails on its own and is announced
    on its own, the last failure re-raises after every mailbox ran."""
    counts, failure = Counter({INGESTED: 0, SKIPPED: 0, QUARANTINED: 0}), None
    mailboxes = Mailbox.objects.select_related("channel").filter(receive_enabled=True).order_by("pk")
    for mailbox in mailboxes.filter(channel=channel) if channel else mailboxes:
        try:
            counts.update(poll_mailbox(mailbox))
        except Exception as error:  # noqa: BLE001 — one broken mailbox must not stop the others
            failure = error
            _alert(mailbox, error)
    if failure is not None:
        raise failure
    return counts


def _alert(mailbox: Mailbox, error: Exception) -> None:
    logger.warning("communicator IMAP poll of mailbox %s failed: %s", mailbox.pk, type(error).__name__)
    day = clock_service.now_for(mailbox.channel).date()
    title = f"IMAP poll of mailbox {mailbox.address} failed"
    alert_service.notify_once(mailbox.channel, kind=f"imap:{mailbox.pk}", severity="medium", title=title, day=day)


def poll_mailbox(mailbox: Mailbox) -> Counter:
    counts = Counter()
    imap = mailbox_service.imap_login(mailbox)
    with imap:
        _examine(imap, mailbox)
        for uid in _new_uids(imap, mailbox)[: communicator_settings.COMMUNICATOR_INBOUND_BATCH]:
            counts[_process_uid(imap, mailbox, uid)] += 1
    mailbox.last_polled_at = timezone.now()
    Mailbox.objects.filter(pk=mailbox.pk).update(last_polled_at=mailbox.last_polled_at)
    return counts


def _quoted(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _require_ok(status: str, command: str) -> None:
    if status != "OK":
        raise ImapStatusError(f"IMAP {command} answered {status}")


def _examine(imap: imaplib.IMAP4, mailbox: Mailbox) -> None:
    """EXAMINE the folder and read its UIDVALIDITY; a changed one restarts the cursor (dedup skips known mail)."""
    status, _ = imap.select(_quoted(mailbox.folder), readonly=True)
    _require_ok(status, "EXAMINE")
    _, data = imap.response("UIDVALIDITY")
    if not data or not data[0]:
        raise ImapStatusError("EXAMINE sent no UIDVALIDITY")
    validity = int(data[0])
    if validity == mailbox.uidvalidity:
        return
    if mailbox.uidvalidity is not None:
        logger.warning("communicator mailbox %s: UIDVALIDITY changed, reading from UID 0", mailbox.pk)
        mailbox.last_uid = 0
    mailbox.uidvalidity = validity
    Mailbox.objects.filter(pk=mailbox.pk).update(uidvalidity=validity, last_uid=mailbox.last_uid)


def _new_uids(imap: imaplib.IMAP4, mailbox: Mailbox) -> list[int]:
    """UIDs above the cursor, oldest first (`n:*` always matches the highest UID, so it is filtered out)."""
    status, data = imap.uid("SEARCH", None, f"UID {mailbox.last_uid + 1}:*")
    _require_ok(status, "SEARCH")
    uids = sorted(int(uid) for uid in (data[0] or b"").split())
    return [uid for uid in uids if uid > mailbox.last_uid]


def _process_uid(imap: imaplib.IMAP4, mailbox: Mailbox, uid: int) -> str:
    """`ingested`, `skipped` (unmatched, or expunged since SEARCH) or `quarantined`; transient errors raise."""
    size = _fetch_size(imap, uid)
    if size is not None and size > communicator_settings.COMMUNICATOR_INBOUND_MAX_BYTES:
        return _quarantine(mailbox, uid, QuarantineReason.OVERSIZED, size)
    raw = _fetch_raw(imap, uid) if size is not None else None
    outcome = _ingest(mailbox, uid, raw) if raw else SKIPPED
    if outcome != QUARANTINED:
        _advance(mailbox, uid)
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


def _ingest(mailbox: Mailbox, uid: int, raw: bytes) -> str:
    """Database outages re-raise (transient); mail the database or the parser can never take is quarantined."""
    try:
        reply = inbound_service.ingest(mailbox.channel, raw)
    except DataError:
        return _quarantine(mailbox, uid, QuarantineReason.DATA_ERROR, len(raw))
    except DatabaseError:
        raise
    except Exception as error:  # noqa: BLE001 — a mail the parser cannot read must not block the mailbox
        logger.warning("communicator mailbox %s: mail uid %s unreadable: %s", mailbox.pk, uid, type(error).__name__)
        return _quarantine(mailbox, uid, QuarantineReason.UNPARSEABLE, len(raw))
    return INGESTED if reply is not None else SKIPPED


def _quarantine(mailbox: Mailbox, uid: int, reason: str, size: int) -> str:
    InboundQuarantine.objects.create(mailbox=mailbox, uid=uid, reason=reason, size=size, received_at=timezone.now())
    logger.warning("communicator mailbox %s: mail uid %s quarantined (%s)", mailbox.pk, uid, reason)
    _advance(mailbox, uid)
    return QUARANTINED


def _advance(mailbox: Mailbox, uid: int) -> None:
    mailbox.last_uid = uid
    Mailbox.objects.filter(pk=mailbox.pk).update(last_uid=uid)
