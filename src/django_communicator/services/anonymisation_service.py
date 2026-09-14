# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Pseudonymisation of recipients: retention (by signal from leads) and GDPR erasure (`django_communicator.gdpr`).
Rows are never deleted and nothing is sent again; queryset updates only. Only the subject's own rows are touched:
outbound messages of threads to the address and replies the address sent — another sender's reply in the same
thread stays as it is."""

from django.db.models import Q, QuerySet

from django_communicator.enums import Direction
from django_communicator.models import Message, Reply, Thread
from django_communicator.utils.emails import anonymised_address, email_hash, normalize_email

ERASED = "[erased]"


def threads_of_hash(subject_ref: str, hashed: str) -> list[int]:
    """Threads about `subject_ref` whose recipient hashes to `hashed` (same hash as leads)."""
    threads = Thread.objects.filter(subject_ref=subject_ref).values_list("pk", "recipient_email")
    return [pk for pk, recipient in threads if email_hash(recipient) == hashed]


def threads_of_email(email: str) -> QuerySet[Thread]:
    """Every thread to the address, including threads already anonymised to its token."""
    return Thread.objects.filter(
        Q(recipient_email__iexact=normalize_email(email)) | Q(recipient_email=anonymised_address(email))
    )


def replies_of_email(email: str) -> QuerySet[Reply]:
    """Replies sent by the address (or already carrying its token), whatever thread they landed in."""
    return Reply.objects.filter(Q(from_email__iexact=normalize_email(email)) | Q(from_email=anonymised_address(email)))


def replies_of_hash(thread_ids: list[int], hashed: str) -> list[int]:
    """Replies in the threads whose sender hashes to `hashed` — the subject's, never a colleague's."""
    replies = Reply.objects.filter(thread_id__in=thread_ids).values_list("pk", "from_email")
    return [pk for pk, sender in replies if email_hash(sender) == hashed]


def outbound_messages(thread_ids: list[int]) -> QuerySet[Message]:
    return Message.objects.filter(thread_id__in=thread_ids, direction=Direction.OUT)


def anonymise_recipients(thread_ids: list[int], token: str, hashed: str) -> dict[str, int]:
    """Recipient of the threads and the subject's replies in them → token; names and reply headers dropped."""
    threads = Thread.objects.filter(pk__in=thread_ids).update(recipient_email=token, recipient_name="")
    reply_ids = replies_of_hash(thread_ids, hashed)
    replies = Reply.objects.filter(pk__in=reply_ids).update(from_email=token, raw_headers={})
    return {"threads": threads, "replies": replies}


def erase_contents(thread_ids: list[int], email: str) -> dict[str, int]:
    """GDPR erasure on top of the anonymisation: subjects, bodies, footers, prompts and render contexts scrubbed."""
    messages = outbound_messages(thread_ids).update(
        subject=ERASED, body_text=ERASED, body_html=ERASED, legal_footer="", rendered_prompt="", render_context={}
    )
    replies = replies_of_email(email).update(
        from_email=anonymised_address(email), subject=ERASED, body_text=ERASED, raw_headers={}
    )
    return {"messages": messages, "replies": replies}
