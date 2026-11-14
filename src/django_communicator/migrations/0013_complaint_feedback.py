# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0012_sequence_autostart"),
    ]

    operations = [
        migrations.AlterField(
            model_name="reply",
            name="kind",
            field=models.CharField(
                choices=[
                    ("reply", "Reply"),
                    ("auto", "Autoresponder"),
                    ("suspected_optout", "Suspected opt-out"),
                    ("bounce_hard", "Hard bounce"),
                    ("bounce_soft", "Soft bounce"),
                    ("complaint", "Spam complaint"),
                ],
                db_index=True,
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="reply",
            name="matched_by",
            field=models.CharField(
                choices=[("header", "Header"), ("sender", "Sender"), ("dsn", "DSN"), ("feedback", "Feedback report")],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="threadsequencestate",
            name="stop_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("replied", "Replied"),
                    ("optout", "Opt-out"),
                    ("bounce", "Bounce"),
                    ("manual", "Manual"),
                    ("finished", "Finished"),
                    ("paused", "Paused"),
                    ("failed", "Failed"),
                    ("suppressed", "Suppressed"),
                    ("complaint", "Spam complaint"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
