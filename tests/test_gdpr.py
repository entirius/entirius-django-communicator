# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import json

import pytest
from django.core.serializers.json import DjangoJSONEncoder
from django.dispatch import Signal
from django.utils import timezone

from django_communicator import gdpr
from django_communicator.enums import MessageStatus, ReplyKind, ReplyMatch
from django_communicator.models import Message, Reply, Suppression, Thread
from django_communicator.services.communicate_service import RecipientData, communicate
from django_communicator.signals import leads_receivers
from django_communicator.utils.emails import anonymised_address, email_hash
from tests.conftest import CHANNEL_IDX

pytestmark = pytest.mark.django_db

EMAIL = "stale@example-stale.test"
REF = "leads.Company:150"


def thread_with_reply(channel, email: str = EMAIL, ref: str = REF) -> Thread:
    thread = Thread.objects.create(channel=channel, subject_ref=ref, recipient_email=email, recipient_name="Stale P")
    Message.objects.create(
        thread=thread, status=MessageStatus.SENT, subject="Hello", body_text="Hello Stale", body_html="<p>Hello</p>",
        legal_footer=f"Your address {email}", render_context={"first_name": "Stale"}, sent_at=timezone.now(),
    )  # fmt: skip
    Reply.objects.create(
        channel=channel, thread=thread, from_email=email, subject="Re: Hello", body_text="Thanks, Stale",
        kind=ReplyKind.REPLY, matched_by=ReplyMatch.SENDER, inbound_message_id=f"<{thread.pk}@mail.test>",
        received_at=timezone.now(), raw_headers={"From": email},
    )  # fmt: skip
    return thread


def test_L16_contact_anonymised_updates_thread_and_reply_recipients(channel):
    thread = thread_with_reply(channel)
    other_ref = thread_with_reply(channel, ref="leads.Company:151")
    colleague = thread_with_reply(channel, email="boss@example-stale.test")
    fake_signal = Signal()
    fake_signal.connect(leads_receivers.on_contact_anonymised)
    token = anonymised_address(EMAIL)

    for _ in range(2):
        fake_signal.send(sender=object, email_hash=email_hash(" Stale@Example-Stale.test "), anonymised_email=token,
                         subject_ref=REF)  # fmt: skip

    thread.refresh_from_db()
    reply = thread.replies.get()
    assert (thread.recipient_email, thread.recipient_name) == (token, "")
    assert (reply.from_email, reply.raw_headers, reply.body_text) == (token, {}, "Thanks, Stale")
    assert thread.messages.get().body_text == "Hello Stale"
    assert Thread.objects.get(pk=other_ref.pk).recipient_email == EMAIL
    assert Thread.objects.get(pk=colleague.pk).recipient_email == "boss@example-stale.test"


def test_L17_gdpr_erase_adds_suppression_and_scrubs_bodies(channel):
    thread = thread_with_reply(channel)
    thread_with_reply(channel, email="boss@example-stale.test", ref="leads.Company:151")

    exported = gdpr.gdpr_export(EMAIL.upper())
    counts = gdpr.gdpr_erase(EMAIL)

    assert [row["recipient_email"] for row in exported["Thread"]] == [EMAIL]
    assert len(exported["Message"]) == len(exported["Reply"]) == 1 and exported["Suppression"] == []
    json.dumps(exported, cls=DjangoJSONEncoder)
    assert counts == {"messages": 1, "replies": 1, "threads": 1, "suppressions": 1}
    message, reply = Message.objects.get(thread=thread), Reply.objects.get(thread=thread)
    assert (message.body_text, message.body_html, message.legal_footer, message.render_context) == (
        "[erased]", "[erased]", "", {},
    )  # fmt: skip
    assert (reply.body_text, reply.from_email) == ("[erased]", anonymised_address(EMAIL))
    assert Suppression.objects.get(channel=channel, value=EMAIL).reason == "gdpr_erase"
    stored = json.dumps(list(Thread.objects.values()) + list(Message.objects.values()) + list(Reply.objects.values()),
                        cls=DjangoJSONEncoder)  # fmt: skip
    assert EMAIL not in stored and "boss@example-stale.test" in stored
    assert gdpr.gdpr_erase(EMAIL)["suppressions"] == 1 and Suppression.objects.count() == 1


def test_communicate_after_erase_is_suppressed(ai_template, toolbox, context):
    thread_with_reply(ai_template.channel)
    gdpr.gdpr_erase(EMAIL)

    for email in (EMAIL, anonymised_address(EMAIL)):
        message = communicate(
            channel_idx=CHANNEL_IDX, template_key="lead.cold.shop", recipient=RecipientData(email=email, language="pl"),
            context=context, subject_ref=REF,
        )  # fmt: skip
        assert message.status == MessageStatus.SUPPRESSED
    assert toolbox["complete"].call_count == 0
