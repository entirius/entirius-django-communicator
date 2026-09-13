# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Inbound mail parsing: RFC 5322 ids, sender, plain-text body, header snapshot (never the raw body)."""

import hashlib
import re
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr
from html import unescape

SUBJECT_MAX_CHARS = 998
_MSG_ID = re.compile(r"<[^<>\s]+>")
_TAG = re.compile(r"<[^>]*>")


def parse(raw: bytes) -> EmailMessage:
    return message_from_bytes(raw, policy=policy.default)


def ids_in(value: str) -> list[str]:
    """Every `<id>` in a header value, angle brackets kept."""
    return _MSG_ID.findall(value)


def header_ids(msg: EmailMessage, name: str) -> list[str]:
    """Every `<id>` of a header (In-Reply-To, References, Message-ID)."""
    return ids_in(str(msg.get(name, "")))


def inbound_message_id(msg: EmailMessage, raw: bytes) -> str:
    """The Message-ID, or a digest of the raw bytes for mail without one."""
    ids = header_ids(msg, "Message-ID")
    return ids[0] if ids else f"<sha256-{hashlib.sha256(raw).hexdigest()}@communicator>"


def sender(msg: EmailMessage) -> str:
    return parseaddr(str(msg.get("From", "")))[1].strip().lower()


def subject(msg: EmailMessage) -> str:
    return str(msg.get("Subject", "")).strip()[:SUBJECT_MAX_CHARS]


def body_text(msg: EmailMessage) -> str:
    """The first text/plain part, else text/html without tags; quoted history is kept."""
    plain = msg.get_body(preferencelist=("plain",))
    if plain is not None:
        return _content(plain)
    html = msg.get_body(preferencelist=("html",))
    return unescape(_TAG.sub("", _content(html))) if html is not None else ""


def _content(part: EmailMessage) -> str:
    try:
        return part.get_content()
    except (LookupError, ValueError):
        return part.get_payload(decode=True).decode("utf-8", errors="replace")


def headers(msg: EmailMessage) -> dict[str, str]:
    """Header names → values; a repeated header keeps its values joined by newlines."""
    snapshot: dict[str, str] = {}
    for name, value in msg.items():
        snapshot[name] = f"{snapshot[name]}\n{value}" if name in snapshot else str(value)
    return snapshot
