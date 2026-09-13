# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Delivery status notifications (RFC 3464): detection and the fields the inbound flow needs.

Only the report's own parts are read — a message/rfc822 attachment is never walked into, so a reply that forwards a
bounce is not a DSN.
"""

from dataclasses import dataclass
from email import message_from_string, policy
from email.message import EmailMessage

from django_communicator.services.mail_parser import header_ids, ids_in


@dataclass(frozen=True)
class Dsn:
    status: str
    action: str
    original_message_id: str
    final_recipient: str

    @property
    def is_hard(self) -> bool:
        return self.status.startswith("5")

    @property
    def is_soft(self) -> bool:
        return self.status.startswith("4")

    @property
    def is_failure(self) -> bool:
        """`Action: failed`; `delayed`, `delivered`, `relayed` and `expanded` report no failure."""
        return self.action == "failed"


def is_dsn(msg: EmailMessage) -> bool:
    """Top-level `multipart/report; report-type=delivery-status` only."""
    report_type = str(msg.get_param("report-type", "")).lower()
    return msg.get_content_type() == "multipart/report" and report_type == "delivery-status"


def parse(msg: EmailMessage) -> Dsn:
    fields = _status_fields(msg)
    return Dsn(
        status=fields.get("status", "").split(" ")[0],
        action=fields.get("action", "").lower(),
        original_message_id=_original_message_id(msg, fields),
        final_recipient=fields.get("final-recipient", "").rpartition(";")[2].strip().lower(),
    )


def _status_fields(msg: EmailMessage) -> dict[str, str]:
    """Per-message and first per-recipient fields of the delivery-status part, lower-cased names."""
    fields: dict[str, str] = {}
    for part in msg.iter_parts():
        if part.get_content_type() != "message/delivery-status":
            continue
        for block in part.get_payload():
            for name, value in block.items():
                fields.setdefault(name.lower(), str(value).strip())
    return fields


def _original_message_id(msg: EmailMessage, fields: dict[str, str]) -> str:
    """Original-Message-ID, else In-Reply-To/References of the report, else the returned message's Message-ID."""
    candidates = ids_in(fields.get("original-message-id", ""))
    candidates += header_ids(msg, "In-Reply-To") + header_ids(msg, "References")
    for returned in _returned_messages(msg):
        candidates += header_ids(returned, "Message-ID")
    return candidates[0] if candidates else ""


def _returned_messages(msg: EmailMessage) -> list[EmailMessage]:
    """The attached original: a message/rfc822 part or its text/rfc822-headers."""
    returned = []
    for part in msg.iter_parts():
        if part.get_content_type() == "message/rfc822":
            returned += part.get_payload()
        elif part.get_content_type() == "text/rfc822-headers":
            returned.append(message_from_string(part.get_content(), policy=policy.default))
    return returned
