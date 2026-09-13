# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fernet encryption with key derived from Django SECRET_KEY.

Copy of the django_contact_forms pattern (never imported across modules). Stores the IMAP password of
`MailboxConfig` at rest. Back up SECRET_KEY safely — losing it means losing the stored passwords.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class EncryptionError(Exception):
    """Encryption or decryption failure (wrong key, malformed payload, etc.)."""


def _get_key() -> bytes:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_value(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EncryptionError(f"Cannot encrypt non-string value: {type(value).__name__}")
    try:
        return Fernet(_get_key()).encrypt(value.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        raise EncryptionError(f"Encryption failed: {exc}") from exc


def decrypt_value(encrypted: str | None) -> str | None:
    if encrypted is None:
        return None
    try:
        return Fernet(_get_key()).decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionError("Decryption failed: invalid token or wrong key") from exc
    except Exception as exc:
        raise EncryptionError(f"Decryption failed: {exc}") from exc
