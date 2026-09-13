# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import ThreadStatus


class Thread(BaseModel):
    """A conversation with one recipient about one opaque `subject_ref` (e.g. "leads.Company:42")."""

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="threads")
    subject_ref = models.CharField(max_length=200, db_index=True)
    recipient_email = models.EmailField()
    recipient_name = models.CharField(max_length=255, blank=True, default="")
    recipient_language = models.ForeignKey(
        "django_regional.Language", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=16, choices=ThreadStatus.choices, default=ThreadStatus.OPEN)
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["channel", "subject_ref", "recipient_email"], name="communicator_thread_ref_idx")
        ]

    def __str__(self) -> str:
        return f"{self.subject_ref} → {self.recipient_email}"
