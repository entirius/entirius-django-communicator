# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Copy of `django_leads.utils.emails` (never import across): leads hashes addresses it anonymises with these,
communicator matches its own columns with the same functions — a normalisation drift is a silent miss."""

import hashlib

from django_communicator import settings as communicator_settings


def normalize_email(raw: str) -> str:
    """The contact dedup key: surrounding whitespace dropped, lower-cased."""
    return (raw or "").strip().lower()


def email_hash(email: str) -> str:
    """sha256 hex of the normalised address — copied verbatim into django_communicator (never import across)."""
    return hashlib.sha256(normalize_email(email).encode()).hexdigest()


def anonymised_address(email: str) -> str:
    """The deterministic token that replaces an address: the same email always gives the same token."""
    return f"anon-{email_hash(email)[:16]}@{communicator_settings.LEADS_ANONYMISED_DOMAIN}"


def is_anonymised(email: str) -> bool:
    """A token address: never deliverable, always suppressed."""
    return normalize_email(email).endswith(f"@{communicator_settings.LEADS_ANONYMISED_DOMAIN}")
