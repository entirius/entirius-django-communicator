# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import migrations, models


class Migration(migrations.Migration):
    """A user may have several mailboxes (one of them the user's default); a footer per (language, brand); the
    single `cc_address` (moved into `cc_addresses` by 0027) goes."""

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
            model_name="mailbox",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_user_default", True), ("user__isnull", False)),
                fields=("channel", "user"),
                name="communicator_mailbox_one_user_default",
            ),
        ),
        migrations.AddConstraint(
            model_name="mailfooter",
            constraint=models.UniqueConstraint(
                fields=("channel", "language", "brand"), name="communicator_footer_brand_unique"
            ),
        ),
        migrations.RemoveField(
            model_name="mailbox",
            name="cc_address",
        ),
    ]
