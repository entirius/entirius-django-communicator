# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0013_reply_intent"),
    ]

    operations = [
        migrations.AddField(
            model_name="reply",
            name="action_applied",
            field=models.JSONField(blank=True, default=dict, help_text="The next-step plan applied to this reply."),
        ),
        migrations.AddField(
            model_name="reply",
            name="needs_review",
            field=models.BooleanField(db_index=True, default=False, help_text="A person must read and decide (T4)."),
        ),
        migrations.AddField(
            model_name="reply",
            name="remind_at",
            field=models.DateTimeField(blank=True, help_text="Alert a person to follow up.", null=True),
        ),
        migrations.AddField(
            model_name="reply",
            name="reminded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="reply",
            name="resume_at",
            field=models.DateTimeField(blank=True, help_text="Out of office: resume the paused sequence.", null=True),
        ),
        migrations.AddField(
            model_name="reply",
            name="resumed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="ReplyIntentAction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                (
                    "intent",
                    models.CharField(
                        choices=[
                            ("interested", "Interested"),
                            ("not_now", "Not now"),
                            ("not_interested", "Not interested"),
                            ("out_of_office", "Out of office"),
                            ("wrong_person", "Wrong person"),
                            ("unsubscribe", "Unsubscribe"),
                            ("unclear", "Unclear"),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "sequence",
                    models.CharField(
                        choices=[("stop", "Stop"), ("pause", "Pause"), ("pause_until_return", "Pause until return")],
                        default="stop",
                        max_length=24,
                    ),
                ),
                (
                    "notify",
                    models.CharField(
                        choices=[("none", "None"), ("low", "Low"), ("medium", "Medium"), ("high", "High")],
                        default="high",
                        max_length=8,
                    ),
                ),
                (
                    "remind_after_days",
                    models.PositiveSmallIntegerField(
                        blank=True,
                        help_text="Alert a person this many days after the reply (out of office: fallback).",
                        null=True,
                    ),
                ),
                (
                    "needs_review",
                    models.BooleanField(default=False, help_text="Flag the reply for a person to read and decide."),
                ),
                (
                    "min_confidence",
                    models.FloatField(
                        blank=True,
                        help_text="Below this the class becomes unclear; empty = the module setting.",
                        null=True,
                    ),
                ),
                (
                    "auto_suppress",
                    models.BooleanField(
                        default=False,
                        help_text="`unsubscribe` only: confirm the opt-out without a person (same path as confirm).",
                    ),
                ),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reply_actions",
                        to="django_communicator.channel",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("channel", "intent"), name="communicator_reply_action_channel_intent"
                    )
                ],
            },
        ),
    ]
