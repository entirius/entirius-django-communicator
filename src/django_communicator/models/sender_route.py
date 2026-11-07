# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import MailTransport


class SenderRoute(BaseModel):
    """Send from one mailbox and copy (CC) the same person's mailbox on the other domain.

    A thread carries only the route `key`; the route is resolved when the mail is built. `daily_cap` limits the
    From mailbox (counted per address, so two routes with one From share it); the warm-up fields raise that limit
    day by day from `warmup_start_cap` by `warmup_step`, up to `daily_cap`. Warm-up is off while
    `warmup_start_cap` is empty.
    """

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="sender_routes")
    key = models.CharField(max_length=64)
    name = models.CharField(max_length=128, blank=True, default="", help_text="Display name in the From header.")
    from_email = models.EmailField()
    cc_email = models.EmailField(blank=True, default="", help_text="The same person's mailbox on the other domain.")
    is_active = models.BooleanField(default=True)
    daily_cap = models.PositiveIntegerField(null=True, blank=True, help_text="Deliveries per day from this From.")
    warmup_start_cap = models.PositiveIntegerField(null=True, blank=True, help_text="Cap on the first warm-up day.")
    warmup_step = models.PositiveIntegerField(default=1, help_text="Cap added per day since the warm-up start.")
    warmup_started_on = models.DateField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True, help_text="Set = no mail leaves by this route (Q15).")
    pause_reason = models.CharField(max_length=255, blank=True, default="")
    health_reset_at = models.DateTimeField(
        null=True, blank=True, help_text="Last manual resume: the auto-pause counts only newer events."
    )

    transport = models.CharField(
        max_length=16,
        choices=MailTransport.choices,
        default=MailTransport.SMTP,
        help_text="smtp = the channel's SMTP connection; gmail_api = send through `mail_account`.",
    )
    mail_account = models.ForeignKey(
        "django_communicator.MailAccount",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="routes",
        help_text="Required for transport gmail_api; its address must be `from_email`.",
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["channel", "key"], name="communicator_sender_route_unique")]

    def __str__(self) -> str:
        return f"{self.key}: {self.from_email}"
