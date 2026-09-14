# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import QuarantineReason


class InboundQuarantine(BaseModel):
    """A mail the poll could never ingest; the cursor moved past it. No body, no headers."""

    mailbox = models.ForeignKey("django_communicator.MailboxConfig", on_delete=models.CASCADE, related_name="+")
    uid = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=16, choices=QuarantineReason.choices)
    size = models.PositiveBigIntegerField(default=0)
    received_at = models.DateTimeField()

    def __str__(self) -> str:
        return f"uid {self.uid} [{self.reason}]"
