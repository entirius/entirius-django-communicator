# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import AlertSeverity, ReplyIntent, ReplySequenceAction


class ReplyClassPlan(BaseModel):
    """What a reply of one class does in a channel; no row = the code default (`reply_plan_service.DEFAULTS`)."""

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="+")
    intent = models.CharField(max_length=24, choices=ReplyIntent.choices)
    sequence = models.CharField(max_length=24, choices=ReplySequenceAction.choices)
    notify = models.CharField(max_length=8, choices=AlertSeverity.choices)
    needs_review = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["channel", "intent"], name="communicator_reply_class_plan_unique")
        ]

    def __str__(self) -> str:
        return f"{self.intent} plan of {self.channel_id}"
