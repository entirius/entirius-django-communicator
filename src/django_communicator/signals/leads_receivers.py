# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""django_leads retention (soft dependency): threads of an anonymised contact follow — connected in `apps.ready()`
only when django_leads is installed."""

import logging

from django_communicator.services import anonymisation_service, suppression_service

logger = logging.getLogger(__name__)


def connect() -> None:
    from django_leads.signals import contact_anonymised

    contact_anonymised.connect(on_contact_anonymised, dispatch_uid="django_communicator.on_contact_anonymised")


def on_contact_anonymised(sender, email_hash: str, anonymised_email: str, subject_ref: str, **kwargs) -> None:
    """The token suppressed on every channel (erase and retention alike), then the threads follow. Idempotent: a
    thread already carrying the token no longer matches the hash. Never raises into leads."""
    try:
        if anonymised_email:
            suppression_service.suppress_token(anonymised_email, reason="contact_anonymised")
        thread_ids = anonymisation_service.threads_of_hash(subject_ref, email_hash)
        anonymisation_service.anonymise_recipients(thread_ids, anonymised_email, email_hash)
    except Exception:
        logger.exception("communicator: anonymising threads of %s failed", subject_ref)
