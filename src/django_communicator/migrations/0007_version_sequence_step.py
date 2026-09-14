# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


def backfill_sequence_step(apps, schema_editor):
    """Edited or rewritten follow-ups inherit the step of their parent chain, one version level per pass."""
    Message = apps.get_model("django_communicator", "Message")
    parent_step = Message.objects.filter(pk=models.OuterRef("parent_id")).values("sequence_step")[:1]
    orphans = Message.objects.filter(sequence_step=None, parent__sequence_step__isnull=False)
    while orphans.update(sequence_step=models.Subquery(parent_step)):
        pass


class Migration(migrations.Migration):
    dependencies = [
        ("django_communicator", "0006_inbound_review"),
    ]

    operations = [
        migrations.RunPython(backfill_sequence_step, migrations.RunPython.noop),
    ]
