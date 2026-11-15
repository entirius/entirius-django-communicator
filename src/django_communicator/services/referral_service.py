# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The referral request: a `wrong_person` reply gets a draft answer asking who the right person is.

A fixed text in the thread's language (Polish or English), no template and no AI; it waits for review like every
draft (`review_required`) and carries `referral_request`, so the owner of the subject may let it reach a contact it
flagged as the wrong person (leads' `recipient_check`). One per thread; the legal footer of the thread's last
outbound message goes with it. Off with `COMMUNICATOR_REFERRAL_REQUEST = False`."""

import logging

from django_communicator import settings as communicator_settings
from django_communicator.enums import Direction, MessageStatus, ReplyIntent
from django_communicator.models import Message, Reply
from django_communicator.services import message_service

logger = logging.getLogger(__name__)

TEXTS = {
    "pl": (
        "Dziękuję za odpowiedź i przepraszam za kłopot.\n\n"
        "Czy mogę prosić o kontakt do osoby, która w Państwa firmie zajmuje się tym tematem? Wystarczy imię i "
        "nazwisko albo adres e-mail — w tej sprawie nie będę już pisać na ten adres."
    ),
    "en": (
        "Thank you for your reply, and sorry for the trouble.\n\n"
        "Could you point me to the person at your company who looks after this? A name or an e-mail address is "
        "enough — I will not write to you about this again."
    ),
}


def language_of(reply: Reply) -> str:
    language = reply.thread.recipient_language
    return "pl" if language is not None and language.iso2.lower() == "pl" else "en"


def subject_for(subject: str) -> str:
    subject = subject.strip()
    return subject if subject.lower().startswith("re:") else f"Re: {subject}".strip()[:255]


def after_reply(reply: Reply) -> Message | None:
    """Runs after the reply's commit; a failure is logged, never raised into the inbound poll."""
    try:
        return request_referral(reply)
    except Exception:
        logger.exception("communicator: referral request for reply %s failed", reply.pk)
        return None


def request_referral(reply: Reply) -> Message | None:
    """The draft, or None for another class, a disabled setting or a thread that already has one."""
    if reply.intent != ReplyIntent.WRONG_PERSON or not communicator_settings.COMMUNICATOR_REFERRAL_REQUEST:
        return None
    thread = reply.thread
    outbound = Message.objects.filter(thread=thread, direction=Direction.OUT)
    if outbound.filter(referral_request=True).exists():
        return None
    last = outbound.exclude(status=MessageStatus.REJECTED).order_by("-pk").first()
    language = language_of(reply)
    return message_service.create_message(
        thread=thread,
        status=MessageStatus.REVIEW_REQUIRED,
        subject=subject_for(reply.subject or (last.subject if last else "")),
        body_text=TEXTS[language],
        legal_footer=last.legal_footer if last else "",
        requires_review=True,
        referral_request=True,
        in_reply_to=reply,
    )
