# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Upgrade from 0.4.x: `migrate` runs the system checks with `databases` BEFORE it applies a migration, so every
check must tolerate a schema older than the models. Tags v0.4.0 and v0.4.1 both end at migration 0011."""

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

APP = "django_communicator"
LAST_04X = [(APP, "0011_mail_footer")]


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def v041_database(transactional_db):
    """A database as 0.4.1 left it, holding a sending channel with one thread and one message."""
    apps = migrate(LAST_04X)
    channel = apps.get_model(APP, "Channel").objects.create(
        idx="default-europe", label="Default", mode="sandbox", sandbox_mailbox="qa@example.test"
    )
    thread = apps.get_model(APP, "Thread").objects.create(
        channel=channel, subject_ref="leads.Company:1", recipient_email="anna@example.test"
    )
    apps.get_model(APP, "Message").objects.create(thread=thread, status="sent", subject="Hello", body_text="Hi")
    yield
    migrate(MigrationExecutor(connection).loader.graph.leaf_nodes(APP))


def test_checks_on_the_old_schema_do_not_crash(v041_database):
    call_command("check", databases=["default"])


def test_migrate_to_head_keeps_the_rows(v041_database):
    call_command("migrate", verbosity=0)

    apps = (
        MigrationExecutor(connection)
        .loader.project_state(MigrationExecutor(connection).loader.graph.leaf_nodes(APP))
        .apps
    )
    channel = apps.get_model(APP, "Channel").objects.get()
    thread = apps.get_model(APP, "Thread").objects.get()
    message = apps.get_model(APP, "Message").objects.get()
    assert (channel.idx, channel.mode) == ("default-europe", "sandbox")
    assert (thread.channel_id, thread.recipient_email) == (channel.pk, "anna@example.test")
    assert (message.thread_id, message.status, message.subject) == (thread.pk, "sent", "Hello")
