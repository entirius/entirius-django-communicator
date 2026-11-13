# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.conf import settings
from django.db import models
from django.db.models import Q
from django_utils.models.base_model import BaseModel

from django_communicator.enums import MailboxAuth, SmtpSecurity
from django_communicator.utils.encrypted_field import EncryptedTextField


class Mailbox(BaseModel):
    """One address of a channel: sends over its own SMTP login, receives over its own IMAP login, has its own daily
    cap (with an optional warm-up) and its own pause. `last_uid` is the IMAP cursor; mail is never flagged."""

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="mailboxes")
    address = models.EmailField(help_text="The From address.")
    display_name = models.CharField(max_length=128, blank=True, default="")
    cc_address = models.EmailField(blank=True, default="", help_text="Copy of every mail sent from this mailbox.")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    is_default = models.BooleanField(default=False)
    auth_method = models.CharField(max_length=16, choices=MailboxAuth.choices, default=MailboxAuth.PASSWORD)

    smtp_host = models.CharField(max_length=255, blank=True, default="")
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_security = models.CharField(max_length=8, choices=SmtpSecurity.choices, default=SmtpSecurity.STARTTLS)
    smtp_user = models.CharField(max_length=255, blank=True, default="")
    smtp_password = EncryptedTextField(blank=True, default="")
    send_enabled = models.BooleanField(default=False)
    smtp_login_failed_at = models.DateTimeField(null=True, blank=True, help_text="SMTP refused the login.")

    imap_host = models.CharField(max_length=255, blank=True, default="")
    imap_port = models.PositiveIntegerField(default=993)
    imap_use_ssl = models.BooleanField(default=True)
    imap_user = models.CharField(max_length=255, blank=True, default="")
    imap_password = EncryptedTextField(blank=True, default="")
    folder = models.CharField(max_length=64, default="INBOX")
    last_uid = models.PositiveBigIntegerField(default=0)
    uidvalidity = models.PositiveBigIntegerField(null=True, blank=True, help_text="UIDVALIDITY the cursor belongs to.")
    receive_enabled = models.BooleanField(default=False)
    last_polled_at = models.DateTimeField(null=True, blank=True)

    daily_cap = models.PositiveIntegerField(null=True, blank=True, help_text="Empty: only the channel cap.")
    warmup_start_cap = models.PositiveIntegerField(null=True, blank=True)
    warmup_step = models.PositiveIntegerField(default=0)
    warmup_started_on = models.DateField(null=True, blank=True)

    paused_at = models.DateTimeField(null=True, blank=True)
    pause_reason = models.CharField(max_length=255, blank=True, default="")
    health_reset_at = models.DateTimeField(null=True, blank=True, help_text="The health policy counts newer events.")

    class Meta:
        verbose_name_plural = "mailboxes"
        constraints = [
            models.UniqueConstraint(fields=["channel", "address"], name="communicator_mailbox_address_unique"),
            models.UniqueConstraint(
                fields=["channel"], condition=Q(is_default=True), name="communicator_mailbox_one_default"
            ),
            models.UniqueConstraint(
                fields=["channel", "user"], condition=Q(user__isnull=False), name="communicator_mailbox_one_per_user"
            ),
        ]

    def __str__(self) -> str:
        return self.address
