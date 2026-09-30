# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`communicator.smtp` health check: a sending channel without its SMTP entry is visible before any send."""

from unittest.mock import patch

import pytest
from django.core import checks

from django_communicator.checks import sending_channels_have_smtp
from django_communicator.enums import ChannelMode
from tests.factories import ChannelFactory

DB = ["default"]


def _no_smtp_entries():
    """`django_email.settings` is frozen at import — `override_settings` never reaches it."""
    return patch("django_email.settings.EMAIL_SMTP_CONFIGURATION_CHANNELS", {})


@pytest.mark.django_db
class TestSendingChannelsHaveSmtp:
    def test_sandbox_channel_without_entry_is_a_high_warning(self):
        ChannelFactory(mode=ChannelMode.SANDBOX, sandbox_mailbox="qa@example.test")
        with _no_smtp_entries():
            [message] = sending_channels_have_smtp(databases=DB)
        assert message.level == checks.WARNING
        assert message.id == "communicator.smtp"
        assert message.obj == {
            "scope": "default-europe",
            "state": "unconfigured",
            "severity": "high",
            "fix_url": message.obj["fix_url"],
        }
        assert message.obj["fix_url"].startswith("https://")
        assert str(message).startswith("default-europe: (communicator.smtp) ")  # CLI label, not the dict

    def test_dry_run_channel_never_sends_so_it_passes(self):
        ChannelFactory()
        with _no_smtp_entries():
            assert sending_channels_have_smtp(databases=DB) == []

    def test_configured_channel_passes(self):
        ChannelFactory(mode=ChannelMode.SANDBOX, sandbox_mailbox="qa@example.test")
        assert sending_channels_have_smtp(databases=DB) == []

    def test_without_databases_no_query_runs(self, django_assert_num_queries):
        with django_assert_num_queries(0):
            assert sending_channels_have_smtp() == []

    def test_before_migrate_the_missing_table_is_skipped(self):
        with (
            _no_smtp_entries(),
            patch("django.db.backends.base.introspection.BaseDatabaseIntrospection.table_names", return_value=[]),
        ):
            assert sending_channels_have_smtp(databases=DB) == []

    def test_registered_under_config_tag(self):
        assert sending_channels_have_smtp in checks.registry.registry.get_checks()
        assert {"entirius_config", "communicator.smtp"} <= set(sending_channels_have_smtp.tags)
