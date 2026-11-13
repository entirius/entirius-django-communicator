# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import AddressReason, AddressState


class AddressStatus(BaseModel):
    """What is known about one address, install-wide: a failed check or a hard bounce. Only `invalid` blocks mail;
    `invalid / hard_bounce` is final (never re-checked)."""

    email = models.CharField(max_length=254, unique=True)
    status = models.CharField(max_length=16, choices=AddressState.choices, default=AddressState.UNKNOWN)
    reason = models.CharField(max_length=32, choices=AddressReason.choices, blank=True, default="")
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.email}: {self.status}"
