# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class SendPolicy(BaseModel):
    """When a channel may send. Timezone and holiday country come from the channel."""

    channel = models.OneToOneField("django_communicator.Channel", on_delete=models.CASCADE, related_name="send_policy")
    business_days_only = models.BooleanField(default=True)
    daily_cap = models.PositiveIntegerField(default=10)
    spread = models.BooleanField(default=True, help_text="Spread the daily cap over the remaining window runs.")

    def __str__(self) -> str:
        return f"Send policy of {self.channel_id}"
