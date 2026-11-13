# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""`MailboxConfig` → `Mailbox`: forward and backward, with one old row and with none."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

APP = "django_communicator"
BEFORE = [(APP, "0015_bulk_approval_log")]
AFTER = [(APP, "0018_remove_mailbox_config")]
SECRET = "imap-1"  # noqa: S105 — test-only


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def raw(table: str, column: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM {table}")  # noqa: S608 — test-only names
        return cursor.fetchone()[0]


@pytest.fixture
def old_state(transactional_db):
    apps = migrate(BEFORE)
    yield apps
    migrate(AFTER)


def test_one_row_forward_and_back(old_state):
    apps = old_state
    channel = apps.get_model(APP, "Channel").objects.create(idx="default-europe", label="Default")
    config = apps.get_model(APP, "MailboxConfig").objects.create(
        channel=channel, imap_host="imap.test", imap_user="u", imap_password=SECRET, last_uid=7, uid_validity=3
    )
    apps.get_model(APP, "InboundQuarantine").objects.create(
        mailbox=config, uid=5, reason="oversized", received_at="2026-09-14T08:00:00Z"
    )
    ciphertext = raw(config._meta.db_table, "imap_password")

    new_apps = migrate(AFTER)

    mailbox = new_apps.get_model(APP, "Mailbox").objects.get()
    assert (mailbox.address, mailbox.display_name, mailbox.is_default, mailbox.send_enabled) == (
        "outreach@mail.example.test",
        "",
        True,
        False,
    )
    assert (mailbox.receive_enabled, mailbox.last_uid, mailbox.uidvalidity, mailbox.imap_host) == (
        True,
        7,
        3,
        "imap.test",
    )
    assert raw(mailbox._meta.db_table, "imap_password") == ciphertext and mailbox.imap_password == SECRET
    assert new_apps.get_model(APP, "InboundQuarantine").objects.get().mailbox_id == mailbox.pk

    back = migrate(BEFORE)

    restored = back.get_model(APP, "MailboxConfig").objects.get()
    assert (restored.imap_user, restored.last_uid, restored.uid_validity, restored.imap_password) == (
        "u",
        7,
        3,
        "imap-1",
    )
    assert back.get_model(APP, "InboundQuarantine").objects.get().mailbox_id == restored.pk


def test_no_row_forward_and_back(old_state):
    new_apps = migrate(AFTER)
    assert not new_apps.get_model(APP, "Mailbox").objects.exists()
    back = migrate(BEFORE)
    assert not back.get_model(APP, "MailboxConfig").objects.exists()


def test_address_falls_back_to_the_imap_user(old_state, settings):
    settings.EMAIL_SMTP_CONFIGURATION_CHANNELS = {}
    apps = old_state
    channel = apps.get_model(APP, "Channel").objects.create(idx="no-entry", label="No entry")
    apps.get_model(APP, "MailboxConfig").objects.create(
        channel=channel, imap_host="imap.test", imap_user="box@mail.example.test", is_active=False
    )

    mailbox = migrate(AFTER).get_model(APP, "Mailbox").objects.get()

    assert (mailbox.address, mailbox.receive_enabled) == ("box@mail.example.test", False)
