# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.db import models
from django.db.models import Q
from django_utils.models.base_model import BaseModel

from django_communicator.enums import SuppressionKind, SuppressionSource


class Suppression(BaseModel):
    """Never contact this email, or anyone at this registrable domain (`value` stored lower-cased). A row without a
    channel is global (every channel): always for kind `email_token` (the token of an erased or anonymised address),
    for `email` / `domain` when written under `COMMUNICATOR_SUPPRESSION_SCOPE = "install"`. `source` says what wrote
    the row, `reason` is a free note."""

    channel = models.ForeignKey(
        "django_communicator.Channel", on_delete=models.CASCADE, null=True, blank=True, related_name="suppressions"
    )
    kind = models.CharField(max_length=16, choices=SuppressionKind.choices)
    value = models.CharField(max_length=254)
    source = models.CharField(max_length=24, choices=SuppressionSource.choices, default=SuppressionSource.MANUAL)
    reason = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["channel", "kind", "value"], name="communicator_suppression_unique"),
            models.UniqueConstraint(
                fields=["kind", "value"],
                condition=Q(channel__isnull=True),
                name="communicator_suppression_global_unique",
            ),
            models.CheckConstraint(
                condition=Q(channel__isnull=False)
                | Q(kind__in=[SuppressionKind.EMAIL_TOKEN, SuppressionKind.EMAIL, SuppressionKind.DOMAIN]),
                name="communicator_suppression_global_kinds",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}: {self.value}"
