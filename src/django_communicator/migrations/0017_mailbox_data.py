# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""One `Mailbox` per `MailboxConfig` row: the channel default, receiving as before, not sending (the channel SMTP
entry keeps sending until a person enables the mailbox). The From address is the channel's `DEFAULT_FROM_EMAIL`
when it can be read, else the IMAP user. Passwords are copied as stored ciphertext, so a value the current key cannot
decrypt survives. The reverse keeps one mailbox per channel (the default first) and drops the quarantine rows of the
others."""

from email.utils import parseaddr

from django.conf import settings
from django.db import migrations

RECEIVE_FIELDS = ("imap_host", "imap_port", "imap_use_ssl", "folder", "last_uid", "last_polled_at")


def _from_address(channel_idx: str) -> tuple[str, str]:
    entry = (getattr(settings, "EMAIL_SMTP_CONFIGURATION_CHANNELS", None) or {}).get(channel_idx) or {}
    name, address = parseaddr(entry.get("DEFAULT_FROM_EMAIL") or "")
    return (name, address) if "@" in address else ("", "")


def _copy_password(schema_editor, source, source_pk: int, target, target_pk: int) -> None:
    quote = schema_editor.quote_name
    sql = (
        f"UPDATE {quote(target._meta.db_table)} SET imap_password = "  # noqa: S608 — table names, values bound
        f"(SELECT imap_password FROM {quote(source._meta.db_table)} WHERE id = %s) WHERE id = %s"
    )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(sql, [source_pk, target_pk])


def forward(apps, schema_editor) -> None:
    config_model = apps.get_model("django_communicator", "MailboxConfig")
    mailbox_model = apps.get_model("django_communicator", "Mailbox")
    quarantine = apps.get_model("django_communicator", "InboundQuarantine")
    for config in config_model.objects.select_related("channel"):
        name, address = _from_address(config.channel.idx)
        mailbox = mailbox_model.objects.create(
            channel_id=config.channel_id,
            address=address or config.imap_user,
            display_name=name,
            is_default=True,
            send_enabled=False,
            imap_user=config.imap_user,
            receive_enabled=config.is_active,
            uidvalidity=config.uid_validity,
            **{field: getattr(config, field) for field in RECEIVE_FIELDS},
        )
        _copy_password(schema_editor, config_model, config.pk, mailbox_model, mailbox.pk)
        quarantine.objects.filter(mailbox_id=config.pk).update(new_mailbox=mailbox)


def backward(apps, schema_editor) -> None:
    config_model = apps.get_model("django_communicator", "MailboxConfig")
    mailbox_model = apps.get_model("django_communicator", "Mailbox")
    quarantine = apps.get_model("django_communicator", "InboundQuarantine")
    kept = set()
    for mailbox in mailbox_model.objects.exclude(imap_host="").order_by("-is_default", "pk"):
        if mailbox.channel_id in kept:
            continue
        kept.add(mailbox.channel_id)
        config = config_model.objects.create(
            channel_id=mailbox.channel_id,
            imap_user=mailbox.imap_user or mailbox.address,  # an empty login meant "the address"
            is_active=mailbox.receive_enabled,
            uid_validity=mailbox.uidvalidity,
            **{field: getattr(mailbox, field) for field in RECEIVE_FIELDS},
        )
        _copy_password(schema_editor, mailbox_model, mailbox.pk, config_model, config.pk)
        quarantine.objects.filter(new_mailbox=mailbox).update(mailbox=config)
    quarantine.objects.filter(mailbox__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("django_communicator", "0016_mailboxes")]

    operations = [migrations.RunPython(forward, backward)]
