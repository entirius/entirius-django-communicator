# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import NotifySeverity, ReplyIntent, ReplySequenceAction


class ReplyIntentAction(BaseModel):
    """What the module does with a reply of one intent class in one channel. No row = the code default
    (`reply_action_service.DEFAULTS`), which only does what a reply did before T4 or less."""

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="reply_actions")
    intent = models.CharField(max_length=24, choices=ReplyIntent.choices)
    sequence = models.CharField(max_length=24, choices=ReplySequenceAction.choices, default=ReplySequenceAction.STOP)
    notify = models.CharField(max_length=8, choices=NotifySeverity.choices, default=NotifySeverity.HIGH)
    remind_after_days = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Alert a person this many days after the reply (out of office: fallback)."
    )
    needs_review = models.BooleanField(default=False, help_text="Flag the reply for a person to read and decide.")
    min_confidence = models.FloatField(
        null=True, blank=True, help_text="Below this the class becomes unclear; empty = the module setting."
    )
    auto_suppress = models.BooleanField(
        default=False, help_text="`unsubscribe` only: confirm the opt-out without a person (same path as confirm)."
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["channel", "intent"], name="communicator_reply_action_channel_intent")
        ]

    def __str__(self) -> str:
        return f"{self.channel_id}:{self.intent}"
