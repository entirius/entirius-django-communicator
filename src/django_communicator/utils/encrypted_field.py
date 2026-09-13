# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Custom Django field that transparently encrypts/decrypts on read/write."""

import logging

from django.core.exceptions import ValidationError
from django.db import models

from django_communicator.utils.encryption import EncryptionError, decrypt_value, encrypt_value

logger = logging.getLogger(__name__)


class EncryptedTextField(models.TextField):
    """TextField that encrypts at rest with Fernet (key derived from SECRET_KEY).

    Cannot be filtered, indexed, or used in aggregate lookups — by design. On decryption failure (typically
    SECRET_KEY rotated without re-encrypting rows) it returns the empty string and logs model.field; the raw
    ciphertext is never returned, so a later save cannot double-encrypt it.
    """

    description = "Text field encrypted at rest with Fernet"

    def __init__(self, *args, **kwargs) -> None:
        if kwargs.get("db_index"):
            raise ValueError("EncryptedTextField cannot be indexed — encrypted data is not searchable")
        if kwargs.get("unique"):
            raise ValueError(
                "EncryptedTextField cannot be unique — Fernet IV randomisation makes ciphertext distinct per save"
            )
        if kwargs.get("primary_key"):
            raise ValueError("EncryptedTextField cannot be a primary key")
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        if value == "":
            return ""
        try:
            return decrypt_value(value)
        except EncryptionError:
            logger.error(
                "EncryptedTextField decryption failed (field=%s.%s) — returning empty string; "
                "row needs admin attention (likely SECRET_KEY rotation without re-encryption)",
                getattr(self.model, "__name__", "?"),
                self.name,
            )
            return ""

    def to_python(self, value):
        return value

    def get_prep_value(self, value):
        if value is None:
            return None
        if value == "":
            return ""
        try:
            return encrypt_value(value)
        except EncryptionError as exc:
            raise ValidationError(f"Failed to encrypt value: {exc}") from exc
