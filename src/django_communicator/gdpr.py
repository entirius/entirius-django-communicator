# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""GDPR hooks (art. 15 export, art. 17 erasure) discovered by `django_leads.gdpr.registry`.

Erasure pseudonymises (rows stay for counters and the audit trail) and suppresses the address's token on every
channel (`email_token`, no channel) — the plain address is kept nowhere, and `communicate()` answers `suppressed`
for it afterwards, whether or not a thread ever existed."""

from typing import Any

from django.db import transaction
from django.db.models import Q

from django_communicator.enums import SuppressionKind
from django_communicator.models import Suppression
from django_communicator.services import anonymisation_service, suppression_service
from django_communicator.utils.emails import anonymised_address, email_hash, normalize_email

THREAD_FIELDS = ("id", "channel__idx", "subject_ref", "recipient_email", "recipient_name", "status", "created_at")
MESSAGE_FIELDS = ("id", "thread_id", "direction", "subject", "body_text", "body_html", "legal_footer",
                  "rendered_prompt", "render_context", "status", "sent_at")  # fmt: skip
REPLY_FIELDS = ("id", "thread_id", "from_email", "subject", "body_text", "kind", "received_at")


def gdpr_export(email: str) -> dict[str, Any]:
    thread_ids = list(anonymisation_service.threads_of_email(email).values_list("pk", flat=True))
    token_rows = Q(kind=SuppressionKind.EMAIL_TOKEN, value=anonymised_address(email))
    suppressions = Suppression.objects.filter(Q(kind=SuppressionKind.EMAIL, value=normalize_email(email)) | token_rows)
    return {
        "Thread": list(anonymisation_service.threads_of_email(email).values(*THREAD_FIELDS)),
        "Message": list(anonymisation_service.outbound_messages(thread_ids).values(*MESSAGE_FIELDS)),
        "Reply": list(anonymisation_service.replies_of_email(email).values(*REPLY_FIELDS)),
        "Suppression": list(suppressions.values("channel__idx", "kind", "reason", "created_at")),
    }


def gdpr_erase(email: str) -> dict[str, int]:
    token = anonymised_address(email)
    thread_ids = list(anonymisation_service.threads_of_email(email).values_list("pk", flat=True))
    with transaction.atomic():
        counts = anonymisation_service.erase_contents(thread_ids, email)
        anonymised = anonymisation_service.anonymise_recipients(thread_ids, token, email_hash(email))
        suppression_service.suppress_token(token, reason="gdpr_erase")
    return {**counts, "threads": anonymised["threads"], "suppressions": 1}
