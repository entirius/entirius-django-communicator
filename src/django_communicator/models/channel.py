# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import holidays
from django.core.exceptions import ValidationError
from django.db import models
from django_utils.models.base_model import BaseModel
from idx_normalizator import validate_idx

from django_communicator.enums import ChannelMode


class Channel(BaseModel):
    """A communication channel. `mode`, `sandbox_mailbox` and `live_enabled` are enforced by the sending layer."""

    idx = models.CharField(max_length=128, unique=True)
    label = models.CharField(max_length=128)
    default_language = models.ForeignKey(
        "django_regional.Language",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="communicator_default_channels",
    )
    languages = models.ManyToManyField("django_regional.Language", blank=True, related_name="communicator_channels")
    timezone = models.CharField(max_length=64, default="Europe/Warsaw")
    country = models.CharField(max_length=2, default="PL")
    mode = models.CharField(max_length=16, choices=ChannelMode.choices, default=ChannelMode.DRY_RUN)
    sandbox_mailbox = models.EmailField(blank=True, default="")
    live_enabled = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.label} [{self.idx}]"

    def clean(self) -> None:
        """A sandbox needs its mailbox and live needs the live flag — a channel is never silently live (C-30)."""
        super().clean()
        if self.mode == ChannelMode.SANDBOX and not self.sandbox_mailbox:
            raise ValidationError({"sandbox_mailbox": "sandbox mode requires a sandbox mailbox"})
        if self.mode == ChannelMode.LIVE and not self.live_enabled:
            raise ValidationError({"live_enabled": "live mode requires live_enabled"})
        self.validate_locale()

    def validate_locale(self) -> None:
        """`country` must be known to `holidays` and `timezone` to `zoneinfo` — the send policy reads both."""
        if self.country not in holidays.list_supported_countries():
            raise ValidationError({"country": f"country {self.country!r} is not supported by holidays"})
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError({"timezone": f"unknown timezone {self.timezone!r}"}) from None

    def save(self, *args, **kwargs) -> None:
        validate_idx(str(self.idx))
        self.validate_locale()
        super().save(*args, **kwargs)
