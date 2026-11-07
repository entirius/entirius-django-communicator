# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel

from django_communicator.enums import MailAccountState, MailProvider
from django_communicator.utils.encrypted_field import EncryptedTextField


class MailAccount(BaseModel):
    """A mailbox the communicator uses through a provider API (Gmail) instead of SMTP / IMAP.

    The refresh token is encrypted at rest (Fernet, key derived from SECRET_KEY, as the IMAP password) and is never
    returned by the API or written to a log. A route with `transport = gmail_api` sends in the name of this account;
    with `inbound_enabled` the account's inbox is also read into the same inbound pipeline as IMAP.
    """

    channel = models.ForeignKey("django_communicator.Channel", on_delete=models.CASCADE, related_name="mail_accounts")
    email = models.EmailField(help_text="The mailbox; the signed-in Google account must be this address.")
    provider = models.CharField(max_length=16, choices=MailProvider.choices, default=MailProvider.GMAIL)
    state = models.CharField(max_length=16, choices=MailAccountState.choices, default=MailAccountState.NOT_CONNECTED)
    refresh_token = EncryptedTextField(blank=True, default="")
    scopes = models.TextField(blank=True, default="", help_text="Space separated scopes Google granted.")
    inbound_enabled = models.BooleanField(default=False, help_text="Read the inbox of this account for replies.")
    connected_at = models.DateTimeField(null=True, blank=True)
    last_ok_at = models.DateTimeField(null=True, blank=True, help_text="Last successful call to the provider.")
    last_error = models.CharField(max_length=255, blank=True, default="", help_text="Short code, never a token.")
    last_error_at = models.DateTimeField(null=True, blank=True)
    history_id = models.CharField(max_length=32, blank=True, default="", help_text="Inbound cursor (Gmail historyId).")
    last_polled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["channel", "email"], name="communicator_mail_account_unique")]

    def __str__(self) -> str:
        return f"{self.email} ({self.provider}, {self.state})"
