# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0013_complaint_feedback"),
    ]

    operations = [
        migrations.AlterField(
            model_name="message",
            name="failure_code",
            field=models.CharField(
                blank=True,
                choices=[
                    ("no_template", "No template"),
                    ("render", "Render"),
                    ("budget", "Budget"),
                    ("schema", "Schema"),
                    ("model", "Model"),
                    ("upstream", "Upstream"),
                    ("smtp", "SMTP"),
                    ("bounce", "Bounce"),
                    ("send_outcome_unknown", "Send outcome unknown"),
                    ("legal_footer", "Legal footer missing"),
                ],
                default="",
                max_length=32,
            ),
        ),
    ]
