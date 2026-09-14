# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import SequenceStopReason


class ThreadSequenceState(BaseModel):
    """Where a thread is in its sequence. `step` counts the follow-ups already scheduled."""

    thread = models.OneToOneField("django_communicator.Thread", on_delete=models.CASCADE, related_name="sequence_state")
    sequence = models.ForeignKey("django_communicator.Sequence", on_delete=models.CASCADE, related_name="+")
    step = models.PositiveSmallIntegerField(default=0)
    next_due_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    stop_reason = models.CharField(max_length=32, choices=SequenceStopReason.choices, blank=True, default="")

    def __str__(self) -> str:
        return f"{self.thread_id} step {self.step}"
