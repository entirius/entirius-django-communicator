# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import Direction, FailureCode, MessageStatus


class Message(BaseModel):
    """One message version. `status` is written only by `services.message_service`."""

    thread = models.ForeignKey("django_communicator.Thread", on_delete=models.CASCADE, related_name="messages")
    direction = models.CharField(max_length=8, choices=Direction.choices, default=Direction.OUT)
    status = models.CharField(max_length=32, choices=MessageStatus.choices, db_index=True)
    template_version = models.ForeignKey(
        "django_communicator.MessageTemplateVersion", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    subject = models.CharField(max_length=255, blank=True, default="")
    body_text = models.TextField(blank=True, default="")
    body_html = models.TextField(blank=True, default="")
    render_context = models.JSONField(default=dict, blank=True)
    rendered_prompt = models.TextField(blank=True, default="")
    model = models.CharField(max_length=128, blank=True, default="")
    usage = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    send_attempts = models.PositiveSmallIntegerField(default=0, help_text="SMTP deliveries tried (4xx retries).")
    version = models.PositiveSmallIntegerField(default=1)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children")
    automated_rewrites = models.PositiveSmallIntegerField(default=0)
    edited_by_human = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    requires_review = models.BooleanField(default=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=255, blank=True, default="")
    review_notes = models.TextField(blank=True, default="")
    legal_footer = models.TextField(blank=True, default="")
    scheduled_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    send_attempted_at = models.DateTimeField(null=True, blank=True, help_text="Wall clock of the `sending` claim.")
    sequence_step = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Set on sequence follow-ups.")
    message_id = models.CharField(max_length=255, blank=True, default="")
    failure_code = models.CharField(max_length=32, choices=FailureCode.choices, blank=True, default="")
    failure_detail = models.TextField(blank=True, default="")
    replied_at = models.DateTimeField(null=True, blank=True)
    bounce_retry_at = models.DateTimeField(null=True, blank=True, help_text="Set by the first soft bounce (4.x.x).")
    delivery_note = models.CharField(max_length=64, blank=True, default="", help_text="Last DSN that changed nothing.")

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"], name="communicator_msg_status_idx")]

    def __str__(self) -> str:
        return f"#{self.pk} v{self.version} [{self.status}] {self.subject}"
