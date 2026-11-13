# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0022_message_in_reply_to"),
    ]

    operations = [
        migrations.AddField(
            model_name="thread",
            name="subject_label",
            field=models.CharField(
                blank=True, default="", help_text="What the thread is about, in the caller's words.", max_length=200
            ),
        ),
    ]
