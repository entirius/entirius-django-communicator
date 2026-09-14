# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import json

import pytest
from django.core.serializers.json import DjangoJSONEncoder
from django.dispatch import Signal
from django.utils import timezone

from django_communicator import gdpr
from django_communicator.enums import MessageStatus, ReplyKind, ReplyMatch, SuppressionKind
from django_communicator.models import Message, Reply, Suppression, Thread
from django_communicator.services import suppression_service
from django_communicator.services.communicate_service import RecipientData, communicate
from django_communicator.signals import leads_receivers
from django_communicator.utils import emails
from django_communicator.utils.emails import anonymised_address, email_hash
from tests.conftest import CHANNEL_IDX, api_url
from tests.factories import ChannelFactory, make_template

pytestmark = pytest.mark.django_db

EMAIL = "stale@example-stale.test"
REF = "leads.Company:150"


TRICKY_EMAILS = (" Foo@Bar.PL ", "UPPER@EXAMPLE.COM", "zoë@exämple.test", "jan+leads@example.com", "\tTab@x.test\n")


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


def reply_from(thread: Thread, email: str) -> Reply:
    return Reply.objects.create(
        channel=thread.channel, thread=thread, from_email=email, subject="Re: Hello", body_text="Colleague here",
        kind=ReplyKind.REPLY, matched_by=ReplyMatch.HEADER, inbound_message_id=f"<{email}@mail.test>",
        received_at=timezone.now(), raw_headers={"From": email},
    )  # fmt: skip


def send_anonymised(email: str = EMAIL, ref: str = REF) -> None:
    fake_signal = Signal()
    fake_signal.connect(leads_receivers.on_contact_anonymised)
    fake_signal.send(sender=object, email_hash=email_hash(email), anonymised_email=anonymised_address(email),
                     subject_ref=ref)  # fmt: skip


def draft_to(email: str, channel_idx: str = CHANNEL_IDX) -> Message:
    return communicate(
        channel_idx=channel_idx, template_key="lead.cold.shop", recipient=RecipientData(email=email, language="pl"),
        context={"company_name": "Example"}, subject_ref=REF,
    )  # fmt: skip


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


def test_L17_gdpr_erase_adds_global_token_suppression_and_scrubs_contents(channel):
    thread = thread_with_reply(channel)
    thread_with_reply(channel, email="boss@example-stale.test", ref="leads.Company:151")

    exported = gdpr.gdpr_export(EMAIL.upper())
    counts = gdpr.gdpr_erase(EMAIL)

    assert [row["recipient_email"] for row in exported["Thread"]] == [EMAIL]
    assert len(exported["Message"]) == len(exported["Reply"]) == 1 and exported["Suppression"] == []
    assert {"render_context", "rendered_prompt", "legal_footer", "body_html"} <= set(exported["Message"][0])
    assert exported["Message"][0]["render_context"] == {"first_name": "Stale"}
    json.dumps(exported, cls=DjangoJSONEncoder)
    assert counts == {"messages": 1, "replies": 1, "threads": 1, "suppressions": 1}
    message, reply = Message.objects.get(thread=thread), Reply.objects.get(thread=thread)
    assert (message.subject, message.body_text, message.body_html, message.legal_footer, message.render_context) == (
        "[erased]", "[erased]", "[erased]", "", {},
    )  # fmt: skip
    assert (reply.subject, reply.body_text, reply.from_email) == ("[erased]", "[erased]", anonymised_address(EMAIL))
    suppression = Suppression.objects.get()
    assert (suppression.channel, suppression.kind, suppression.value) == (
        None, SuppressionKind.EMAIL_TOKEN, anonymised_address(EMAIL),
    )  # fmt: skip
    stored = json.dumps(
        [*Thread.objects.values(), *Message.objects.values(), *Reply.objects.values(), *Suppression.objects.values()],
        cls=DjangoJSONEncoder,
    )
    assert EMAIL not in stored and "boss@example-stale.test" in stored
    assert gdpr.gdpr_erase(EMAIL)["suppressions"] == 1 and Suppression.objects.count() == 1
    assert [row["kind"] for row in gdpr.gdpr_export(EMAIL)["Suppression"]] == [SuppressionKind.EMAIL_TOKEN]


def test_L17_erased_never_contacted_address_is_suppressed_on_every_channel(ai_template, toolbox):
    other = ChannelFactory(idx="default-local", label="Default Local")
    make_template(other)
    thread_with_reply(ai_template.channel, email="colleague@example-stale.test")

    gdpr.gdpr_erase(EMAIL)

    for channel_idx in (CHANNEL_IDX, "default-local"):
        assert draft_to(f" {EMAIL.upper()} ", channel_idx).status == MessageStatus.SUPPRESSED
    assert suppression_service.is_suppressed(other, EMAIL)
    assert not suppression_service.is_suppressed(other, "colleague@example-stale.test")
    assert toolbox["complete"].call_count == 0


def test_L16_retention_signal_suppresses_the_token_globally(channel):
    send_anonymised("never-contacted@example-stale.test", ref="leads.Company:999")
    send_anonymised("never-contacted@example-stale.test", ref="leads.Company:999")

    row = Suppression.objects.get()
    assert (row.channel, row.value) == (None, anonymised_address("never-contacted@example-stale.test"))
    assert suppression_service.is_suppressed(ChannelFactory(idx="default-local"), "Never-Contacted@example-stale.test")


def test_L17_third_party_reply_in_thread_not_exported_or_rewritten(channel):
    thread = thread_with_reply(channel)
    colleague = reply_from(thread, "boss@example-stale.test")
    elsewhere = thread_with_reply(channel, email="boss@example-stale.test", ref="leads.Company:151")
    own_reply_elsewhere = reply_from(elsewhere, EMAIL)

    exported = gdpr.gdpr_export(EMAIL)
    gdpr.gdpr_erase(EMAIL)
    send_anonymised()

    assert sorted(row["id"] for row in exported["Reply"]) == sorted([thread.replies.first().pk, own_reply_elsewhere.pk])
    assert colleague.pk not in [row["id"] for row in exported["Reply"]]
    colleague.refresh_from_db()
    assert (colleague.from_email, colleague.body_text, colleague.subject) == (
        "boss@example-stale.test", "Colleague here", "Re: Hello",
    )  # fmt: skip
    own_reply_elsewhere.refresh_from_db()
    assert (own_reply_elsewhere.from_email, own_reply_elsewhere.body_text) == (anonymised_address(EMAIL), "[erased]")
    assert Thread.objects.get(pk=elsewhere.pk).recipient_email == "boss@example-stale.test"


def test_suppressions_list_includes_global_tokens_and_filters_by_value(channel, admin_api):
    suppression_service.create_suppression(channel, kind="email", value="a@example.test")
    suppression_service.suppress_token(anonymised_address(EMAIL), reason="gdpr_erase")

    listed = admin_api.get(api_url("suppressions/")).json()["results"]
    filtered = admin_api.get(api_url(f"suppressions/?value={anonymised_address(EMAIL).upper()}")).json()["results"]

    assert len(listed) == 2
    assert [(row["kind"], row["value"]) for row in filtered] == [("email_token", anonymised_address(EMAIL))]


def test_token_parity_with_django_leads():
    leads_emails = pytest.importorskip("django_leads.utils.emails", reason="django_leads is not installed")
    for raw in TRICKY_EMAILS:
        assert emails.email_hash(raw) == leads_emails.email_hash(raw)
        assert emails.anonymised_address(raw) == leads_emails.anonymised_address(raw)


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
