# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""`communicator_suppressions --to-install [--apply]`: global rows for the channel email / domain rows, for an install
that switches `COMMUNICATOR_SUPPRESSION_SCOPE` to `install`. Dry run unless `--apply`; idempotent."""

from django.core.management.base import BaseCommand, CommandError

from django_communicator.services import suppression_service


class Command(BaseCommand):
    help = "Copy channel email / domain suppressions to global rows (dry run unless --apply)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--to-install", action="store_true", help="Copy channel rows to global rows.")
        parser.add_argument("--dry-run", action="store_true", default=True, help="Only count (the default).")
        parser.add_argument("--apply", action="store_true", help="Write the global rows.")

    def handle(self, *args, **options) -> None:
        if not options["to_install"]:
            raise CommandError("nothing to do: pass --to-install")
        count = suppression_service.copy_to_install(apply=options["apply"])
        verb = "created" if options["apply"] else "would be created (dry run, pass --apply)"
        self.stdout.write(f"{count} global suppression rows {verb}")
