# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class SequenceStep(BaseModel):
    sequence = models.ForeignKey("django_communicator.Sequence", on_delete=models.CASCADE, related_name="steps")
    number = models.PositiveSmallIntegerField()
    days_after_previous = models.PositiveSmallIntegerField()
    template_key = models.SlugField(max_length=128)

    class Meta:
        ordering = ["sequence", "number"]
        constraints = [models.UniqueConstraint(fields=["sequence", "number"], name="communicator_sequence_step_unique")]

    def __str__(self) -> str:
        return f"{self.sequence_id} #{self.number}"
