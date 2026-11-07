# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.db import models
from django_utils.models.base_model import BaseModel


class BulkApprovalLog(BaseModel):
    """Who approved or rejected a batch of drafts from the approval inbox, and what came of each id."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    action = models.CharField(max_length=16)
    reason = models.CharField(max_length=255, blank=True, default="")
    requested_ids = models.JSONField(default=list)
    done_ids = models.JSONField(default=list)
    failed = models.JSONField(default=dict, help_text="Message id → why it was not changed.")

    def __str__(self) -> str:
        return f"{self.action} by {self.user_id}: {len(self.done_ids)}/{len(self.requested_ids)}"
