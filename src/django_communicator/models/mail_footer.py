# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class MailFooter(BaseModel):
    """The HTML footer of a channel's mail in one language; sanitised and validated by `services.footer_service`.

    `html` carries the `{{ legal }}` placeholder exactly once — the legal text the caller passes replaces it."""

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="mail_footers")
    language = models.ForeignKey("django_regional.Language", on_delete=models.PROTECT, related_name="+")
    html = models.TextField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["channel", "language"], name="communicator_footer_unique")]

    def __str__(self) -> str:
        return f"{self.channel_id}/{self.language_id}"
