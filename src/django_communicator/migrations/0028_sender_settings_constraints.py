# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


def keep_what_the_old_schema_holds(apps, schema_editor):
    """Reverse only, runs before the old constraints come back: per (channel, user) the oldest mailbox keeps its
    user, the others are detached (`user` set to NULL; the mailbox, its threads and its logins stay, but it is nobody's
    personal mailbox any more); every footer with a brand is deleted (the footer without a brand stays)."""
    Mailbox = apps.get_model("django_communicator", "Mailbox")
    seen = set()
    for mailbox in Mailbox.objects.filter(user__isnull=False).order_by("pk"):
        key = (mailbox.channel_id, mailbox.user_id)
        if key in seen:
            Mailbox.objects.filter(pk=mailbox.pk).update(user=None)
        seen.add(key)
    apps.get_model("django_communicator", "MailFooter").objects.exclude(brand="").delete()


class Migration(migrations.Migration):
    """A user may have several mailboxes; a footer per (language, brand). Rolling back loses data the old schema cannot
    hold: see `keep_what_the_old_schema_holds`."""

    dependencies = [
        ("django_communicator", "0027_sender_settings"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="mailbox",
            name="communicator_mailbox_one_per_user",
        ),
        migrations.RemoveConstraint(
            model_name="mailfooter",
            name="communicator_footer_unique",
        ),
        migrations.AddConstraint(
            model_name="mailfooter",
            constraint=models.UniqueConstraint(
                fields=("channel", "language", "brand"), name="communicator_footer_brand_unique"
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, keep_what_the_old_schema_holds),
    ]
