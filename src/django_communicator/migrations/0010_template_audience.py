# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import django.core.validators
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError


def refuse_audience_variants(apps, schema_editor):
    """Reverse guard: without `audience` the variants would collide on the old (channel, key, language) key."""
    template = apps.get_model("django_communicator", "MessageTemplate")
    if template.objects.exclude(audience="").exists():
        raise IrreversibleError(
            "Cannot unapply django_communicator 0010: templates with a non-blank audience exist. "
            "Delete the audience variants (MessageTemplate.audience != '') first, then migrate back."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0009_message_draft_retries"),
    ]

    operations = [
        migrations.AddField(
            model_name="messagetemplate",
            name="audience",
            field=models.CharField(
                blank=True,
                default="",
                max_length=64,
                validators=[
                    django.core.validators.RegexValidator(
                        "^[A-Z0-9_]*$", "Upper-case code, e.g. RETAILER; blank for every audience."
                    )
                ],
            ),
        ),
        migrations.RemoveConstraint(
            model_name="messagetemplate",
            name="communicator_template_unique_key",
        ),
        migrations.AddConstraint(
            model_name="messagetemplate",
            constraint=models.UniqueConstraint(
                fields=("channel", "key", "language", "audience"), name="communicator_template_unique_key_audience"
            ),
        ),
        # last forward = first on reverse: refuses before the old unique constraint is re-added
        migrations.RunPython(migrations.RunPython.noop, refuse_audience_variants),
    ]
