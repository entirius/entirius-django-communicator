# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0008_global_email_token_suppression"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="draft_retries",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Automatic retries of a draft that failed transiently (toolbox down, timeout, 5xx).",
            ),
        ),
    ]
