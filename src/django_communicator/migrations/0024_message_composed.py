# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0023_thread_subject_label"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="composed",
            field=models.BooleanField(default=False, help_text="Written by hand through `compose()`."),
        ),
    ]
