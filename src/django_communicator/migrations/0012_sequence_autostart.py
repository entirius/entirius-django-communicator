# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0011_mail_footer"),
    ]

    operations = [
        migrations.AddField(
            model_name="sequence",
            name="autostart",
            field=models.BooleanField(
                default=False,
                help_text="Starts by itself after the first delivered message of a thread that has no sequence. One per channel.",
            ),
        ),
        migrations.AddConstraint(
            model_name="sequence",
            constraint=models.UniqueConstraint(
                condition=models.Q(("autostart", True)), fields=("channel",), name="communicator_sequence_one_autostart"
            ),
        ),
    ]
