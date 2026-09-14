# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.db import models


class MessageTemplateVersion(models.Model):
    """Immutable snapshot of a template's content; drafts point at the version they were rendered with."""

    template = models.ForeignKey(
        "django_communicator.MessageTemplate", on_delete=models.CASCADE, related_name="versions"
    )
    number = models.PositiveIntegerField()
    subject = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField()
    json_schema = models.JSONField(null=True, blank=True)
    model = models.CharField(max_length=128, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["template", "number"], name="communicator_template_version_unique")
        ]

    def __str__(self) -> str:
        return f"{self.template_id} v{self.number}"
