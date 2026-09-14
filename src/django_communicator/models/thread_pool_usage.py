# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class ThreadPoolUsage(BaseModel):
    """A pool text already used in a thread."""

    thread = models.ForeignKey("django_communicator.Thread", on_delete=models.CASCADE, related_name="+")
    text = models.ForeignKey("django_communicator.TextPool", on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["thread", "text"], name="communicator_pool_usage_unique")]
