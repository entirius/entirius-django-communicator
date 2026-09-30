# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0010_template_audience"),
        ("django_regional", "0002_reset_pk_sequences"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="footer_html",
            field=models.TextField(
                blank=True,
                default="",
                help_text="The HTML footer as sent, legal text included; empty = none or not sent.",
            ),
        ),
        migrations.CreateModel(
            name="MailFooter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                ("html", models.TextField()),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mail_footers",
                        to="django_communicator.channel",
                    ),
                ),
                (
                    "language",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="+", to="django_regional.language"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("channel", "language"), name="communicator_footer_unique")
                ],
            },
        ),
    ]
