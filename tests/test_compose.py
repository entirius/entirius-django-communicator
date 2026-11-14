# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""`compose()`: a hand-written message — refusals, thread reuse, sequence stop, reply threading, delivery."""

import random
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from django_communicator.enums import (
    FailureCode,
    MessageStatus,
    ReplyKind,
    ReplyMatch,
    SequenceStopReason,
    SuppressionKind,
    ThreadStatus,
)
from django_communicator.models import (
    Mailbox,
    Message,
    Reply,
    Sequence,
    SequenceStep,
    Suppression,
    TextPool,
    Thread,
    ThreadSequenceState,
)
from django_communicator.services import clock_service, send_service, sequence_service
from django_communicator.services.communicate_service import (
    LegalFooterRequiredError,
    RecipientData,
    ThreadMismatchError,
)
from django_communicator.services.compose_service import ComposeError, compose
from django_communicator.signals import message_approved
from tests.conftest import CHANNEL_IDX, MONDAY_10, api_url, approved_message
from tests.factories import make_mailbox, make_static

EMAIL = "jan@example-shop-1.test"
REF = "send:1"
FOOTER = "Example Sp. z o.o., KRS 0000000000"


@pytest.fixture
def author(db):
    return get_user_model().objects.create_user(username="anna", is_staff=True)


@pytest.fixture
def sequence(channel) -> Sequence:
    sequence = Sequence.objects.create(channel=channel, key="followup")
    SequenceStep.objects.create(sequence=sequence, number=1, days_after_previous=3, template_key="followup")
    TextPool.objects.create(sequence=sequence, body="TEST follow-up")
    return sequence


def recipient(footer: str = FOOTER, email: str = EMAIL) -> RecipientData:
    return RecipientData(email=email, first_name="Jan", last_name="Kowalski", language="pl", legal_footer=footer)


def write(author, *, footer: str = FOOTER, **overrides) -> Message:
    fields = {"subject": "Our call", "body_text": "Hello Jan,\n\nas promised <b>today</b>.", "subject_ref": REF}
    return compose(channel_idx=CHANNEL_IDX, recipient=recipient(footer), author=author, **{**fields, **overrides})


def run() -> dict:
    return dict(send_service.run_send_due())


def reply_to(thread: Thread, message_id: str = "<reply-1@example-shop-1.test>") -> Reply:
    Thread.objects.filter(pk=thread.pk).update(status=ThreadStatus.REPLIED)
    return Reply.objects.create(
        channel=thread.channel,
        thread=thread,
        from_email=thread.recipient_email,
        subject="Re: Hi",
        body_text="Call me tomorrow.",
        kind=ReplyKind.REPLY,
        matched_by=ReplyMatch.HEADER,
        inbound_message_id=message_id,
        received_at=timezone.now(),
    )


# --- refusals ---


def test_suppressed_recipient_gets_a_suppressed_message_and_no_mail(policy, sandbox, author):
    Suppression.objects.create(channel=policy.channel, kind=SuppressionKind.EMAIL, value=EMAIL)

    message = write(author, footer="")

    assert (message.status, message.failure_detail, message.reviewed_by) == (
        MessageStatus.SUPPRESSED,
        "suppressed",
        None,
    )
    assert run()["sent"] == 0 and mail.outbox == []


def test_missing_footer_creates_nothing(channel, author):
    with pytest.raises(LegalFooterRequiredError):
        write(author, footer="  ")

    assert not Thread.objects.exists() and not Message.objects.exists()


@pytest.mark.parametrize(("subject", "body"), [("", "Hi"), ("x" * 256, "Hi"), ("Hi", " \n"), ("Hi", "x" * 20_001)])
def test_blank_or_too_long_text_creates_nothing(channel, author, subject, body):
    with pytest.raises(ComposeError):
        write(author, subject=subject, body_text=body)

    assert not Message.objects.exists()


def test_body_limit_is_a_setting(channel, author, monkeypatch):
    monkeypatch.setattr("django_communicator.settings.COMMUNICATOR_COMPOSE_MAX_CHARS", 5)
    with pytest.raises(ComposeError, match="1 to 5 characters"):
        write(author, body_text="123456")


def test_given_thread_of_another_subject_is_refused(channel, author):
    other = Thread.objects.create(channel=channel, subject_ref="other:1", recipient_email=EMAIL)

    with pytest.raises(ThreadMismatchError):
        write(author, thread=other)

    assert not Message.objects.exists()


def test_reply_of_another_thread_is_refused(channel, author):
    first = Thread.objects.create(channel=channel, subject_ref=REF, recipient_email=EMAIL)
    second = Thread.objects.create(channel=channel, subject_ref=REF, recipient_email=EMAIL)

    with pytest.raises(ThreadMismatchError):
        write(author, thread=first, in_reply_to=reply_to(second))


# --- the message and its delivery ---


def test_message_is_approved_by_its_author_and_due_now(policy, author, django_capture_on_commit_callbacks):
    approved = []
    message_approved.connect(lambda sender, message, **kw: approved.append(message.pk), weak=False, dispatch_uid="t")
    with django_capture_on_commit_callbacks(execute=True):
        message = write(author)
    message_approved.disconnect(dispatch_uid="t")

    assert message.status == MessageStatus.APPROVED and message.template_version is None
    assert (message.reviewed_by, message.created_by, message.edited_by_human) == (author, author, True)
    assert message.scheduled_at == MONDAY_10 and message.body_html == "" and message.render_context == {}
    assert approved == [message.pk]


def test_sandbox_delivers_one_mail_with_the_footer_from_the_mailbox(policy, sandbox, author):
    make_mailbox(policy.channel, user=author, display_name="Anna Nowak")

    message = write(author, mailbox_user_id=author.pk)

    assert run()["sent"] == 1
    (sent,) = mail.outbox
    assert sent.from_email == "Anna Nowak <anna@mail.example.test>"
    assert sent.to == ["sandbox@mail.example.test"] and sent.extra_headers["X-Original-To"] == EMAIL
    assert sent.body.startswith("Hello Jan,") and sent.body.endswith(f"-- \n{FOOTER}")
    html = sent.alternatives[0].content
    assert "&lt;b&gt;today&lt;/b&gt;" in html and "<b>" not in html
    message.refresh_from_db()
    assert message.status == MessageStatus.SENT


def test_paused_mailbox_defers_the_message(policy, sandbox, author):
    box = make_mailbox(policy.channel, user=author)
    Mailbox.objects.filter(pk=box.pk).update(paused_at=MONDAY_10)

    message = write(author, mailbox_user_id=author.pk)

    assert run()["deferred"] == 1 and mail.outbox == []
    message.refresh_from_db()
    assert message.status == MessageStatus.APPROVED


def test_daily_cap_is_respected(policy, sandbox, static_template, author):
    policy.daily_cap = 1
    policy.save()
    approved_message(email="ewa@example-shop-2.test", subject_ref="other:1")

    message = write(author)

    first, second = run(), run()

    assert (first["sent"], first["deferred"], second["sent"]) == (1, 1, 0)
    message.refresh_from_db()
    assert message.status == MessageStatus.APPROVED and len(mail.outbox) == 1


def test_delivery_refuses_a_template_less_message_without_a_footer(policy, sandbox, author):
    message = write(author)
    Message.objects.filter(pk=message.pk).update(legal_footer="")

    assert run()["failed"] == 1
    message.refresh_from_db()
    assert (message.status, message.failure_code) == (MessageStatus.FAILED, FailureCode.LEGAL_FOOTER)
    assert mail.outbox == []


# --- threads ---


def test_replied_thread_is_reused_and_open_again(policy, author):
    replied = Thread.objects.create(channel=policy.channel, subject_ref=REF, recipient_email=EMAIL)
    reply_to(replied)

    message = write(author)

    replied.refresh_from_db()
    assert message.thread_id == replied.pk and replied.status == ThreadStatus.OPEN


def test_closed_thread_is_not_reused(policy, author):
    closed = Thread.objects.create(
        channel=policy.channel, subject_ref=REF, recipient_email=EMAIL, status=ThreadStatus.CLOSED
    )

    message = write(author)

    assert message.thread_id != closed.pk and message.thread.status == ThreadStatus.OPEN


def test_answer_to_a_reply_threads_under_it(policy, sandbox, static_template, author):
    cold = approved_message(footer=FOOTER, subject_ref=REF)
    run()
    reply = reply_to(cold.thread)

    write(author, in_reply_to=reply)
    run()

    first, answer = mail.outbox
    assert answer.extra_headers["In-Reply-To"] == "<reply-1@example-shop-1.test>"
    references = answer.extra_headers["References"].split()
    assert references == [first.extra_headers["Message-ID"], "<reply-1@example-shop-1.test>"]


# --- sequences ---


def test_running_sequence_stops_manual_and_never_restarts(policy, sandbox, static_template, sequence, author):
    cold = approved_message(footer=FOOTER, subject_ref=REF)
    run()
    state = sequence_service.start_sequence(cold.thread, sequence)

    write(author)
    run()
    clock_service.set_override(policy.channel, MONDAY_10 + timedelta(days=10))

    assert sequence_service.run_follow_ups(random.Random(0)) == 0
    state.refresh_from_db()
    assert (state.stop_reason, state.next_due_at) == (SequenceStopReason.MANUAL, None)
    assert state.stopped_at is not None and len(mail.outbox) == 2


def test_paused_sequence_is_stopped_manual(policy, static_template, sequence, author):
    cold = approved_message(footer=FOOTER, subject_ref=REF)
    sequence_service.start_sequence(cold.thread, sequence)
    sequence_service.pause_sequence(cold.thread)

    write(author)

    assert ThreadSequenceState.objects.get(thread=cold.thread).stop_reason == SequenceStopReason.MANUAL


def test_delivered_hand_written_message_never_autostarts(policy, sandbox, sequence, author):
    Sequence.objects.filter(pk=sequence.pk).update(autostart=True)

    message = write(author)
    run()

    assert not ThreadSequenceState.objects.filter(thread=message.thread).exists()


def test_delivered_template_less_message_does_not_rearm(policy, static_template, sequence):
    cold = approved_message(footer=FOOTER, subject_ref=REF)
    run()
    state = sequence_service.start_sequence(cold.thread, sequence)
    later = Message.objects.create(
        thread=cold.thread,
        status=MessageStatus.SENT,
        subject="Hi",
        body_text="x",
        sent_at=MONDAY_10 + timedelta(days=2),
    )

    sequence_service.on_delivered(later)

    state.refresh_from_db()
    assert state.next_due_at == MONDAY_10 + timedelta(days=3)


def test_follow_up_context_comes_from_the_newest_templated_message(policy, sandbox, sequence, author):
    make_static(policy.channel, key="followup", auto_approve=True, body="{body} for {company_name}")
    cold = approved_message(footer=FOOTER, subject_ref=REF, context={"company_name": "Example Shop", "body": "Cold."})
    run()
    write(author)  # the hand-written message: no context, its own footer
    Message.objects.filter(thread=cold.thread, template_version=None).update(legal_footer="Hand footer.")
    state = sequence_service.start_sequence(cold.thread, sequence)
    clock_service.set_override(policy.channel, MONDAY_10 + timedelta(days=3))

    follow_up = sequence_service.schedule_follow_up(state, random.Random(0))

    assert follow_up.status == MessageStatus.APPROVED
    assert follow_up.body_text == "TEST follow-up for Example Shop" and follow_up.legal_footer == FOOTER


# --- development view ---


def test_dev_compose_endpoint(policy, admin_api):
    body = {
        "recipient": {"email": EMAIL, "legal_footer": FOOTER},
        "subject": "Hi",
        "body_text": "Hello",
        "subject_ref": REF,
    }

    created = admin_api.post(api_url("test/compose/"), body, format="json")
    no_footer = {**body, "recipient": {"email": EMAIL}}
    unknown_thread = admin_api.post(api_url("test/compose/"), {**body, "thread_id": 0}, format="json")

    assert (created.status_code, created.json()["status"]) == (201, "approved")
    assert Message.objects.get().reviewed_by == admin_api.user
    assert admin_api.post(api_url("test/compose/"), no_footer, format="json").status_code == 400
    assert admin_api.post(api_url("test/compose/"), {**body, "subject": ""}, format="json").status_code == 400
    assert unknown_thread.status_code == 404


def test_dev_compose_refused_outside_development(policy, admin_api):
    body = {
        "recipient": {"email": EMAIL, "legal_footer": FOOTER},
        "subject": "Hi",
        "body_text": "Hello",
        "subject_ref": REF,
    }
    with override_settings(ENVIRONMENT="production"):
        assert admin_api.post(api_url("test/compose/"), body, format="json").status_code == 404
