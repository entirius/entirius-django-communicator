# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.models.mailbox import brand_validator


class MailFooter(BaseModel):
    """The HTML footer of a channel's mail in one language; sanitised and validated by `services.footer_service`.

    `html` carries the `{{ legal }}` placeholder exactly once — the legal text the caller passes replaces it — and
    optionally `{{ unsubscribe }}` once, where the unsubscribe link goes. A blank `brand` is the channel-wide footer."""

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="mail_footers")
    language = models.ForeignKey("django_regional.Language", on_delete=models.PROTECT, related_name="+")
    brand = models.CharField(max_length=32, blank=True, default="", validators=[brand_validator])
    html = models.TextField()
    unsubscribe_label = models.CharField(
        max_length=120, blank=True, default="", help_text="Text of the unsubscribe link; blank: default per language."
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["channel", "language", "brand"], name="communicator_footer_brand_unique")
        ]

    def __str__(self) -> str:
        base = f"{self.channel_id}/{self.language_id}"
        return f"{base}/{self.brand}" if self.brand else base
