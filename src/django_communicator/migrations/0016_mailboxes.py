# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Mailboxes, the health policy and the pause log; `InboundQuarantine` gets a column for its new mailbox (filled by
0017, renamed by 0018)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import django_communicator.utils.encrypted_field


def _base_fields() -> list:
    return [
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("modified_at", models.DateTimeField(auto_now=True)),
    ]


def _user() -> models.ForeignKey:
    return models.ForeignKey(
        blank=True,
        null=True,
        on_delete=django.db.models.deletion.SET_NULL,
        related_name="+",
        to=settings.AUTH_USER_MODEL,
    )


MAILBOX_FIELDS = [
    ("address", models.EmailField(help_text="The From address.", max_length=254)),
    ("display_name", models.CharField(blank=True, default="", max_length=128)),
    (
        "cc_address",
        models.EmailField(
            blank=True, default="", help_text="Copy of every mail sent from this mailbox.", max_length=254
        ),
    ),
    ("is_default", models.BooleanField(default=False)),
    ("auth_method", models.CharField(choices=[("password", "Password")], default="password", max_length=16)),
    ("smtp_host", models.CharField(blank=True, default="", max_length=255)),
    ("smtp_port", models.PositiveIntegerField(default=587)),
    (
        "smtp_security",
        models.CharField(choices=[("starttls", "STARTTLS"), ("ssl", "SSL/TLS")], default="starttls", max_length=8),
    ),
    ("smtp_user", models.CharField(blank=True, default="", max_length=255)),
    ("smtp_password", django_communicator.utils.encrypted_field.EncryptedTextField(blank=True, default="")),
    ("send_enabled", models.BooleanField(default=False)),
    ("smtp_login_failed_at", models.DateTimeField(blank=True, help_text="SMTP refused the login.", null=True)),
    ("imap_host", models.CharField(blank=True, default="", max_length=255)),
    ("imap_port", models.PositiveIntegerField(default=993)),
    ("imap_use_ssl", models.BooleanField(default=True)),
    ("imap_user", models.CharField(blank=True, default="", max_length=255)),
    ("imap_password", django_communicator.utils.encrypted_field.EncryptedTextField(blank=True, default="")),
    ("folder", models.CharField(default="INBOX", max_length=64)),
    ("last_uid", models.PositiveBigIntegerField(default=0)),
    (
        "uidvalidity",
        models.PositiveBigIntegerField(blank=True, help_text="UIDVALIDITY the cursor belongs to.", null=True),
    ),
    ("receive_enabled", models.BooleanField(default=False)),
    ("last_polled_at", models.DateTimeField(blank=True, null=True)),
    ("daily_cap", models.PositiveIntegerField(blank=True, help_text="Empty: only the channel cap.", null=True)),
    ("warmup_start_cap", models.PositiveIntegerField(blank=True, null=True)),
    ("warmup_step", models.PositiveIntegerField(default=0)),
    ("warmup_started_on", models.DateField(blank=True, null=True)),
    ("paused_at", models.DateTimeField(blank=True, null=True)),
    ("pause_reason", models.CharField(blank=True, default="", max_length=255)),
    (
        "health_reset_at",
        models.DateTimeField(blank=True, help_text="The health policy counts newer events.", null=True),
    ),
    (
        "channel",
        models.ForeignKey(
            on_delete=django.db.models.deletion.CASCADE, related_name="mailboxes", to="django_communicator.channel"
        ),
    ),
    ("user", _user()),
]

FAILURE_CODES = [
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
    ("mailbox", "Mailbox cannot send"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0015_bulk_approval_log"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="message",
            name="failure_code",
            field=models.CharField(blank=True, choices=FAILURE_CODES, default="", max_length=32),
        ),
        migrations.CreateModel(
            name="Mailbox",
            fields=_base_fields() + MAILBOX_FIELDS,
            options={
                "verbose_name_plural": "mailboxes",
                "constraints": [
                    models.UniqueConstraint(fields=("channel", "address"), name="communicator_mailbox_address_unique"),
                    models.UniqueConstraint(
                        condition=models.Q(("is_default", True)),
                        fields=("channel",),
                        name="communicator_mailbox_one_default",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("user__isnull", False)),
                        fields=("channel", "user"),
                        name="communicator_mailbox_one_per_user",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="HealthPolicy",
            fields=_base_fields()
            + [
                ("enabled", models.BooleanField(default=False)),
                ("window_hours", models.PositiveIntegerField(default=168)),
                ("min_sample", models.PositiveIntegerField(default=50)),
                (
                    "max_bounce_pct",
                    models.DecimalField(decimal_places=2, default=2, help_text="Hard bounces.", max_digits=5),
                ),
                ("max_optout_pct", models.DecimalField(decimal_places=2, default=3, max_digits=5)),
                ("max_complaint_pct", models.DecimalField(decimal_places=2, default="0.1", max_digits=5)),
                (
                    "channel",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="health_policy",
                        to="django_communicator.channel",
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="MailboxPauseLog",
            fields=_base_fields()
            + [
                ("action", models.CharField(choices=[("pause", "Pause"), ("resume", "Resume")], max_length=8)),
                (
                    "trigger",
                    models.CharField(
                        choices=[
                            ("bounce", "Hard bounces"),
                            ("optout", "Opt-outs"),
                            ("complaint", "Spam complaints"),
                            ("manual", "Manual"),
                        ],
                        max_length=16,
                    ),
                ),
                ("reason", models.CharField(blank=True, default="", max_length=255)),
                ("metrics", models.JSONField(blank=True, default=dict)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="+", to="django_communicator.channel"
                    ),
                ),
                (
                    "mailbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pause_logs",
                        to="django_communicator.mailbox",
                    ),
                ),
                ("user", _user()),
            ],
            options={
                "indexes": [models.Index(fields=["channel", "-created_at"], name="communicator_pauselog_idx")],
            },
        ),
        migrations.AddField(
            model_name="thread",
            name="mailbox",
            field=models.ForeignKey(
                blank=True,
                help_text="Set when the first message is created, never changed; empty = the channel SMTP entry.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="threads",
                to="django_communicator.mailbox",
            ),
        ),
        migrations.AlterField(
            model_name="inboundquarantine",
            name="mailbox",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="django_communicator.mailboxconfig",
            ),
        ),
        migrations.AddField(
            model_name="inboundquarantine",
            name="new_mailbox",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="django_communicator.mailbox",
            ),
        ),
    ]
