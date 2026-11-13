# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django_utils.models.base_model import BaseModel


class ReplyClassProfile(BaseModel):
    """Prompt and model that classify the human replies of a channel. No profile, or an inactive one, means no
    classification. The prompt is never logged and never returned in a list."""

    channel = models.OneToOneField(
        "django_communicator.Channel", on_delete=models.CASCADE, related_name="reply_class_profile"
    )
    prompt_text = models.TextField()
    model = models.CharField(max_length=128)
    min_confidence = models.FloatField(default=0.7, validators=[MinValueValidator(0), MaxValueValidator(1)])
    max_chars = models.PositiveIntegerField(default=3000)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"reply classes of {self.channel_id}"
