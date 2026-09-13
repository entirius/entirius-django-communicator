# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class Sequence(BaseModel):
    """Follow-up steps sent in a thread while the recipient does not reply."""

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="sequences")
    key = models.SlugField(max_length=64)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["channel", "key"], name="communicator_sequence_key_unique")]

    def __str__(self) -> str:
        return self.key
