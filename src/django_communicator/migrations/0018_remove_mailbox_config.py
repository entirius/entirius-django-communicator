# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`InboundQuarantine.mailbox` points at `Mailbox`; `MailboxConfig` is gone."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("django_communicator", "0017_mailbox_data")]

    operations = [
        migrations.RemoveField(model_name="inboundquarantine", name="mailbox"),
        migrations.RenameField(model_name="inboundquarantine", old_name="new_mailbox", new_name="mailbox"),
        migrations.AlterField(
            model_name="inboundquarantine",
            name="mailbox",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="+", to="django_communicator.mailbox"
            ),
        ),
        migrations.DeleteModel(name="MailboxConfig"),
    ]
