# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class TextPool(BaseModel):
    """One follow-up text of a sequence; rendered as `context["body"]` of the step template."""

    sequence = models.ForeignKey("django_communicator.Sequence", on_delete=models.CASCADE, related_name="texts")
    body = models.TextField()
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.body[:50]
