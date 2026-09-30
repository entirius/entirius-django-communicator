# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import TemplateKind

# A SlugField would reject the dotted keys ("lead.cold.shop").
validate_template_key = RegexValidator(r"^[a-z0-9_-]+(\.[a-z0-9_-]+)*$", "Lower-case dotted key, e.g. lead.cold.shop.")
# The caller's audience code (leads passes the lead type), opaque here; blank = every audience.
validate_audience = RegexValidator(r"^[A-Z0-9_]*$", "Upper-case code, e.g. RETAILER; blank for every audience.")


class MessageTemplate(BaseModel):
    """A message by key and language. Content changes are versioned by `services.template_service`.

    `body` is the static body, or for `ai_prompt` the system prompt, a line `=== USER ===` and the user prompt.
    `audience` narrows the template to one audience of the caller; blank serves every audience (`resolve` cascade).
    """

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="templates")
    key = models.CharField(max_length=128, validators=[validate_template_key], help_text="e.g. lead.cold.shop")
    kind = models.CharField(max_length=16, choices=TemplateKind.choices)
    language = models.ForeignKey("django_regional.Language", on_delete=models.PROTECT, related_name="+")
    audience = models.CharField(max_length=64, blank=True, default="", validators=[validate_audience])
    subject = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField()
    json_schema = models.JSONField(null=True, blank=True)
    model = models.CharField(max_length=128, blank=True, default="")
    requires_legal_footer = models.BooleanField(default=True)
    auto_approve = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    current_version = models.ForeignKey(
        "django_communicator.MessageTemplateVersion", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "key", "language", "audience"], name="communicator_template_unique_key_audience"
            )
        ]

    def __str__(self) -> str:
        audience = f" @{self.audience}" if self.audience else ""
        return f"{self.key}{audience} [{self.kind}]"

    def clean(self) -> None:
        if self.kind == TemplateKind.STATIC and (self.json_schema or self.model):
            raise ValidationError("json_schema and model apply to ai_prompt templates only.")
        if self.kind == TemplateKind.AI_PROMPT and self.auto_approve:
            raise ValidationError("auto_approve applies to static templates only.")
        if self.kind == TemplateKind.AI_PROMPT and not self.model:
            raise ValidationError("ai_prompt templates need a toolbox model.")
