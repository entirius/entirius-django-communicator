# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import IntentSource, ReplyIntent, ReplyKind, ReplyMatch


class Reply(BaseModel):
    """One inbound mail attached to a thread. Headers only in `raw_headers`; the body lives in `body_text`."""

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="+")
    thread = models.ForeignKey("django_communicator.Thread", on_delete=models.CASCADE, related_name="replies")
    message = models.ForeignKey(
        "django_communicator.Message", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
    )
    from_email = models.EmailField()
    subject = models.CharField(max_length=998, blank=True, default="")
    body_text = models.TextField(blank=True, default="")
    kind = models.CharField(max_length=24, choices=ReplyKind.choices, db_index=True)
    matched_by = models.CharField(max_length=16, choices=ReplyMatch.choices)
    inbound_message_id = models.CharField(max_length=998)
    received_at = models.DateTimeField()
    raw_headers = models.JSONField(default=dict, blank=True)
    optout_confirmed_at = models.DateTimeField(null=True, blank=True)
    optout_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # The class of a human reply (blank = not classified) and what its plan did; see `reply_class_service`.
    intent = models.CharField(max_length=24, choices=ReplyIntent.choices, blank=True, default="", db_index=True)
    intent_confidence = models.FloatField(null=True, blank=True)
    intent_source = models.CharField(max_length=16, choices=IntentSource.choices, blank=True, default="")
    intent_return_date = models.DateField(null=True, blank=True)
    needs_review = models.BooleanField(default=False, db_index=True)
    plan_applied = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name_plural = "replies"
        constraints = [
            models.UniqueConstraint(fields=["channel", "inbound_message_id"], name="communicator_reply_inbound_uniq")
        ]

    def __str__(self) -> str:
        return f"{self.kind} from {self.from_email}"
