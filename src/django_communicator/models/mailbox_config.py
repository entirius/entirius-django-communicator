# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.utils.encrypted_field import EncryptedTextField


class MailboxConfig(BaseModel):
    """The IMAP mailbox the inbound poll reads for a channel. `last_uid` is the cursor; mail is never flagged."""

    channel = models.OneToOneField(
        "django_communicator.Channel", on_delete=models.CASCADE, related_name="mailbox_config"
    )
    imap_host = models.CharField(max_length=255)
    imap_port = models.PositiveIntegerField(default=993)
    imap_use_ssl = models.BooleanField(default=True)
    imap_user = models.CharField(max_length=255)
    imap_password = EncryptedTextField(blank=True, default="")
    folder = models.CharField(max_length=64, default="INBOX")
    last_uid = models.PositiveBigIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.imap_user}@{self.imap_host}/{self.folder}"
