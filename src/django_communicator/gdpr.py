# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""GDPR hooks (art. 15 export, art. 17 erasure) discovered by `django_leads.gdpr.registry`.

Erasure pseudonymises (rows stay for counters and the audit trail) and suppresses the address on every channel
that wrote to it — that suppression is the only place the plain address remains, so `communicate()` answers
`suppressed` afterwards."""

from typing import Any

from django.db import transaction

from django_communicator.enums import SuppressionKind
from django_communicator.models import Channel, Message, Suppression
from django_communicator.services import anonymisation_service, suppression_service
from django_communicator.utils.emails import anonymised_address, normalize_email

THREAD_FIELDS = ("id", "channel__idx", "subject_ref", "recipient_email", "recipient_name", "status", "created_at")
MESSAGE_FIELDS = ("id", "thread_id", "direction", "subject", "body_text", "status", "sent_at")
REPLY_FIELDS = ("id", "thread_id", "from_email", "subject", "body_text", "kind", "received_at")


def gdpr_export(email: str) -> dict[str, Any]:
    threads = anonymisation_service.threads_of_email(email)
    thread_ids = list(threads.values_list("pk", flat=True))
    replies = anonymisation_service.replies_of_email(email, thread_ids)
    suppressions = Suppression.objects.filter(kind=SuppressionKind.EMAIL, value=normalize_email(email))
    return {
        "Thread": list(threads.values(*THREAD_FIELDS)),
        "Message": list(Message.objects.filter(thread_id__in=thread_ids).values(*MESSAGE_FIELDS)),
        "Reply": list(replies.values(*REPLY_FIELDS)),
        "Suppression": list(suppressions.values("channel__idx", "reason", "created_at")),
    }


def gdpr_erase(email: str) -> dict[str, int]:
    thread_ids = list(anonymisation_service.threads_of_email(email).values_list("pk", flat=True))
    channels = list(Channel.objects.filter(threads__pk__in=thread_ids).distinct())
    with transaction.atomic():
        counts = anonymisation_service.erase_contents(thread_ids, email)
        counts["threads"] = anonymisation_service.anonymise_recipients(thread_ids, anonymised_address(email))["threads"]
        for channel in channels:
            suppression_service.suppress_email(channel, email, reason="gdpr_erase")
    return {**counts, "suppressions": len(channels)}
