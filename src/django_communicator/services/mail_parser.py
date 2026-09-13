# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Inbound mail parsing: RFC 5322 ids, sender, plain-text body, header snapshot (never the raw body).

Every value that reaches the database is stripped of NUL characters and cut to its column limit.
"""

import hashlib
import re
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr
from html import unescape

from django.utils import timezone

SUBJECT_MAX_CHARS = 998
HEADER_MAX_CHARS = 998
EMAIL_MAX_CHARS = 254
_MSG_ID = re.compile(r"<[^<>\s]+>")
_TAG = re.compile(r"<[^>]*>")
_QUOTE_INTRO = re.compile(r"^\s*(on\s.*wrote:|w dniu\s.*pisze:)\s*$", re.IGNORECASE)


def parse(raw: bytes) -> EmailMessage:
    return message_from_bytes(raw, policy=policy.default)


def clean(value: str, limit: int) -> str:
    """NUL characters removed, cut to `limit` characters."""
    return value.replace("\x00", "")[:limit]


def ids_in(value: str) -> list[str]:
    """Every `<id>` in a header value, angle brackets kept."""
    return [clean(found, HEADER_MAX_CHARS) for found in _MSG_ID.findall(value.replace("\x00", ""))]


def header_ids(msg: EmailMessage, name: str) -> list[str]:
    """Every `<id>` of a header (In-Reply-To, References, Message-ID)."""
    return ids_in(str(msg.get(name, "")))


def inbound_message_id(msg: EmailMessage, raw: bytes) -> str:
    """The Message-ID, or a digest of the raw bytes for mail without one."""
    ids = header_ids(msg, "Message-ID")
    return ids[0] if ids else f"<sha256-{hashlib.sha256(raw).hexdigest()}@communicator>"


def sender(msg: EmailMessage) -> str:
    return clean(parseaddr(str(msg.get("From", "")))[1].strip().lower(), EMAIL_MAX_CHARS)


def subject(msg: EmailMessage) -> str:
    return clean(str(msg.get("Subject", "")).strip(), SUBJECT_MAX_CHARS)


def sent_at(msg: EmailMessage) -> datetime | None:
    """The `Date` header as an aware datetime; None when missing or unreadable."""
    try:
        value = msg.get("Date")
        parsed = value.datetime if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None
    if parsed is None or timezone.is_aware(parsed):
        return parsed
    return timezone.make_aware(parsed, UTC)


def body_text(msg: EmailMessage) -> str:
    """The first text/plain part, else text/html without tags; quoted history is kept."""
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        return _content(plain)
    html = msg.get_body(preferencelist=("html",))
    return unescape(_TAG.sub("", _content(html))) if html is not None else ""


def unquoted(text: str) -> str:
    """The reply's own text: lines above "On … wrote:" / "W dniu … pisze:", without `>` quoted lines."""
    lines = []
    for line in text.splitlines():
        if _QUOTE_INTRO.match(line):
            break
        if not line.lstrip().startswith(">"):
            lines.append(line)
    return "\n".join(lines)


def _content(part: EmailMessage) -> str:
    try:
        return part.get_content()
    except (LookupError, ValueError):
        return part.get_payload(decode=True).decode("utf-8", errors="replace")


def headers(msg: EmailMessage) -> dict[str, str]:
    """Header names → values (each cut to 998 characters); a repeated header keeps its values joined by newlines."""
    snapshot: dict[str, str] = {}
    for name, value in msg.items():
        name, value = clean(name, HEADER_MAX_CHARS), clean(str(value), HEADER_MAX_CHARS)
        snapshot[name] = f"{snapshot[name]}\n{value}" if name in snapshot else value
    return snapshot
