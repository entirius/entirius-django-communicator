# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""U3: a `wrong_person` reply gets a draft asking for the right person — in review like every draft, once per thread,
after the owner's receivers (which withdraw drafts to a wrong person) ran."""

import pytest
from django.test import override_settings

from django_communicator.enums import MessageStatus, ReplyIntent
from django_communicator.models import Message
from django_communicator.services import referral_service, review_service
from django_communicator.signals import reply_received
from tests.conftest import api_url
from tests.test_reply_classes import given, ingest_given, sent

pytestmark = pytest.mark.django_db

EMAIL = "owner@example-shop-1.test"


def referrals():
    return Message.objects.filter(referral_request=True)


def test_wrong_person_reply_drafts_a_referral_request(channel):
    message = sent(channel)
    Message.objects.filter(pk=message.pk).update(legal_footer="Legal: Agency Ltd")
    reply, _ = ingest_given(message, "Nie zajmuję się tym.", given(ReplyIntent.WRONG_PERSON))
    draft = referrals().get()
    assert draft.status == MessageStatus.REVIEW_REQUIRED and draft.requires_review
    assert draft.thread_id == message.thread_id and draft.in_reply_to_id == reply.pk
    assert draft.subject == "Re: test reply"
    assert "kontakt do osoby" in draft.body_text  # the thread is Polish
    assert draft.legal_footer == "Legal: Agency Ltd"


def test_english_thread_gets_english_text(channel):
    message = sent(channel)
    message.thread.recipient_language.iso2 = "EN"
    message.thread.recipient_language.save()
    ingest_given(message, "Not me.", given(ReplyIntent.WRONG_PERSON))
    assert "Could you point me to the person" in referrals().get().body_text


@pytest.mark.parametrize("intent", [ReplyIntent.INTERESTED, ReplyIntent.NOT_NOW, ReplyIntent.UNCLEAR])
def test_other_classes_draft_nothing(channel, intent):
    ingest_given(sent(channel), "Hello.", given(intent))
    assert not referrals().exists()


def test_one_referral_per_thread(channel):
    message = sent(channel)
    ingest_given(message, "Not me.", given(ReplyIntent.WRONG_PERSON))
    reply, _ = ingest_given(message, "Still not me.", given(ReplyIntent.WRONG_PERSON))
    assert referrals().count() == 1
    assert referral_service.request_referral(reply) is None


@override_settings()
def test_setting_off_drafts_nothing(channel, monkeypatch):
    from django_communicator import settings as communicator_settings

    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_REFERRAL_REQUEST", False)
    ingest_given(sent(channel), "Not me.", given(ReplyIntent.WRONG_PERSON))
    assert not referrals().exists()


def test_the_referral_is_drafted_after_the_receivers_withdrew_drafts(channel):
    """Like leads: the receiver withdraws every draft to the address; the referral comes after and survives."""
    message = sent(channel)

    def withdraw(sender, thread, reply, **kwargs):
        review_service.withdraw(thread.channel, thread.recipient_email, reason="the contact is the wrong person")

    reply_received.connect(withdraw, dispatch_uid="test.withdraw")
    try:
        ingest_given(message, "Not me.", given(ReplyIntent.WRONG_PERSON))
    finally:
        reply_received.disconnect(dispatch_uid="test.withdraw")
    assert referrals().get().status == MessageStatus.REVIEW_REQUIRED


def test_a_failure_never_breaks_the_inbound(channel, monkeypatch):
    def boom(reply):
        raise RuntimeError("x")

    monkeypatch.setattr(referral_service, "request_referral", boom)
    reply, _ = ingest_given(sent(channel), "Not me.", given(ReplyIntent.WRONG_PERSON))
    assert reply.pk and not referrals().exists()


def test_review_api_shows_the_flag(channel, admin_api):
    ingest_given(sent(channel), "Not me.", given(ReplyIntent.WRONG_PERSON))
    draft = referrals().get()
    body = admin_api.get(api_url(f"review/{draft.pk}/")).json()
    assert body["referral_request"] is True
