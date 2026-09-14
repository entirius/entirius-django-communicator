# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.exceptions import ValidationError
from django.db import models
from django_utils.models.base_model import BaseModel


class SendWindow(BaseModel):
    """An hour window of a send policy, in the channel timezone (start inclusive, end exclusive)."""

    policy = models.ForeignKey("django_communicator.SendPolicy", on_delete=models.CASCADE, related_name="windows")
    order = models.PositiveSmallIntegerField(default=0)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["policy", "order", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")), name="sendwindow_end_after_start"
            )
        ]

    def __str__(self) -> str:
        return f"{self.start_time:%H:%M}-{self.end_time:%H:%M}"

    def clean(self) -> None:
        super().clean()
        if self.end_time <= self.start_time:
            raise ValidationError({"end_time": "end_time must be after start_time"})
