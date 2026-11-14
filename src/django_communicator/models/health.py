# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import HealthTrigger, PauseAction


class HealthPolicy(BaseModel):
    """Auto-pause thresholds of a channel, off by default. A mailbox is paused when, within the last `window_hours`,
    it sent at least `min_sample` mails and a rate (percent of sent) is above its limit."""

    channel = models.OneToOneField(
        "django_communicator.Channel", on_delete=models.CASCADE, related_name="health_policy"
    )
    enabled = models.BooleanField(default=False)
    window_hours = models.PositiveIntegerField(default=168)
    min_sample = models.PositiveIntegerField(default=50)
    max_bounce_pct = models.DecimalField(max_digits=5, decimal_places=2, default=2, help_text="Hard bounces.")
    max_optout_pct = models.DecimalField(max_digits=5, decimal_places=2, default=3)
    max_complaint_pct = models.DecimalField(max_digits=5, decimal_places=2, default="0.1")

    def __str__(self) -> str:
        return f"health policy of {self.channel_id} ({'on' if self.enabled else 'off'})"


class MailboxPauseLog(BaseModel):
    """Every pause and resume of a mailbox: automatic pauses carry the measured rates, manual ones a reason."""

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="+")
    mailbox = models.ForeignKey("django_communicator.Mailbox", on_delete=models.CASCADE, related_name="pause_logs")
    action = models.CharField(max_length=8, choices=PauseAction.choices)
    trigger = models.CharField(max_length=16, choices=HealthTrigger.choices)
    reason = models.CharField(max_length=255, blank=True, default="")
    metrics = models.JSONField(default=dict, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        indexes = [models.Index(fields=["channel", "-created_at"], name="communicator_pauselog_idx")]

    def __str__(self) -> str:
        return f"{self.action} {self.mailbox_id} ({self.trigger})"
