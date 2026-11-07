# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0012_sequence_autostart"),
    ]

    operations = [
        migrations.AddField(
            model_name="reply",
            name="intent",
            field=models.CharField(
                blank=True,
                choices=[
                    ("interested", "Interested"),
                    ("not_now", "Not now"),
                    ("not_interested", "Not interested"),
                    ("out_of_office", "Out of office"),
                    ("wrong_person", "Wrong person"),
                    ("unsubscribe", "Unsubscribe"),
                    ("unclear", "Unclear"),
                ],
                db_index=True,
                default="",
                help_text="Empty = not classified (toolbox off or failed, auto reply, bounce).",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="reply",
            name="intent_confidence",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="reply",
            name="intent_return_date",
            field=models.DateField(blank=True, help_text="Out of office: the day they are back.", null=True),
        ),
        migrations.AddField(
            model_name="reply",
            name="intent_source",
            field=models.CharField(blank=True, default="", help_text="`toolbox` when classified.", max_length=16),
        ),
    ]
