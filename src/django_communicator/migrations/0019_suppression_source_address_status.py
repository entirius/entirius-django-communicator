# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`Suppression.source` from today's `reason` text where it is unambiguous, else `manual` (the reverse drops the
column, `reason` stays the free note); `AddressStatus`; global rows only for the kinds that may be global."""

from django.db import migrations, models

EXACT_SOURCES = {
    "optout confirmed": "optout_confirmed",
    "unsubscribe link": "unsubscribe_link",
    "complaint": "complaint",
    "gdpr_erase": "erased",
    "contact_anonymised": "erased",
}
BOUNCE_PREFIXES = ("dsn ", "smtp ")


def set_sources(apps, schema_editor) -> None:
    Suppression = apps.get_model("django_communicator", "Suppression")
    for reason, source in EXACT_SOURCES.items():
        Suppression.objects.filter(reason=reason).update(source=source)
    for prefix in BOUNCE_PREFIXES:
        Suppression.objects.filter(reason__startswith=prefix).update(source="bounce")


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0018_remove_mailbox_config"),
    ]

    operations = [
        migrations.CreateModel(
            name="AddressStatus",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("modified_at", models.DateTimeField(auto_now=True)),
                ("email", models.CharField(max_length=254, unique=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("unknown", "Unknown"),
                            ("valid", "Valid"),
                            ("risky", "Risky"),
                            ("invalid", "Invalid"),
                        ],
                        default="unknown",
                        max_length=16,
                    ),
                ),
                (
                    "reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("syntax", "Syntax"),
                            ("disposable", "Disposable domain"),
                            ("domain_not_found", "Domain not found"),
                            ("null_mx", "Null MX"),
                            ("no_mail_server", "No mail server"),
                            ("mx_ok", "MX found"),
                            ("no_mx_address_fallback", "No MX, address record only"),
                            ("hard_bounce", "Hard bounce"),
                            ("dns_error", "DNS error"),
                        ],
                        default="",
                        max_length=32,
                    ),
                ),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.AddField(
            model_name="suppression",
            name="source",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("optout_confirmed", "Opt-out confirmed"),
                    ("unsubscribe_link", "Unsubscribe link"),
                    ("bounce", "Hard bounce"),
                    ("complaint", "Spam complaint"),
                    ("erased", "Erased or anonymised"),
                ],
                default="manual",
                max_length=24,
            ),
        ),
        migrations.RunPython(set_sources, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="suppression",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("channel__isnull", False), ("kind__in", ["email_token", "email", "domain"]), _connector="OR"
                ),
                name="communicator_suppression_global_kinds",
            ),
        ),
    ]
