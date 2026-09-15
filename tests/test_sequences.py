# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import logging
import random
import smtplib
from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail

from django_communicator.enums import SequenceStopReason, ThreadStatus
from django_communicator.models import (
    Message,
    MessageTemplate,
    Sequence,
    SequenceStep,
    TextPool,
    Thread,
    ThreadPoolUsage,
    ThreadSequenceState,
)
from django_communicator.services import (
    clock_service,
    counter_service,
    review_service,
    send_service,
    sequence_service,
)
from django_communicator.signals import sequence_finished
from tests.conftest import MONDAY_10, approved_message

THURSDAY_10 = MONDAY_10 + timedelta(days=3)


@pytest.fixture
def sequence(channel) -> Sequence:
    sequence = Sequence.objects.create(channel=channel, key="followup")
    for number, days in ((1, 3), (2, 5)):
        SequenceStep.objects.create(sequence=sequence, number=number, days_after_previous=days, template_key="followup")
    for n in range(1, 4):
        TextPool.objects.create(sequence=sequence, body=f"TEST follow-up {n}/3")
    return sequence


@pytest.fixture
def body_template(channel):
    from tests.factories import make_static

    return make_static(channel, auto_approve=True, body="{body}")


def _sent_cold(sequence, email: str = "jan@example-shop-1.test", subject_ref: str = "send:1") -> ThreadSequenceState:
    cold = approved_message(email=email, subject_ref=subject_ref, footer="Footer.", context={"body": "Cold."})
    send_service.run_send_due()
    return sequence_service.start_sequence(cold.thread, sequence)


def test_C27_pool_exhausted_random_with_warning_seeded(policy, sandbox, body_template, sequence, caplog):
    state = _sent_cold(sequence)
    texts = list(sequence.texts.order_by("pk"))
    ThreadPoolUsage.objects.bulk_create(ThreadPoolUsage(thread=state.thread, text=t) for t in texts[:2])

    fresh = sequence_service.pick_text(state, random.Random(3))
    ThreadPoolUsage.objects.create(thread=state.thread, text=texts[2])
    with caplog.at_level(logging.WARNING):
        reused = [sequence_service.pick_text(state, random.Random(seed)) for seed in (1, 1, 2)]

    assert fresh == texts[2]
    assert reused[0] == reused[1] and {text.pk for text in reused} <= {text.pk for text in texts}
    assert "used the whole pool" in caplog.text


def test_C28_follow_up_references_chain_and_counter(policy, sandbox, body_template, sequence):
    state = _sent_cold(sequence)
    assert state.next_due_at == MONDAY_10 + timedelta(days=3)
    thursday = MONDAY_10 + timedelta(days=3)
    clock_service.set_override(policy.channel, thursday)

    created = sequence_service.run_follow_ups(random.Random(0))
    send_service.run_send_due()

    first, follow_up = mail.outbox
    state.refresh_from_db()
    assert created == 1 and follow_up.subject == "[SANDBOX] Hi Jan"
    assert follow_up.body.startswith("TEST follow-up") and follow_up.body.endswith("-- \nFooter.")
    assert follow_up.extra_headers["References"] == first.extra_headers["Message-ID"]
    assert follow_up.extra_headers["In-Reply-To"] == first.extra_headers["Message-ID"]
    assert counter_service.sent_on(policy.channel, thursday.date()) == 1
    assert (state.step, state.next_due_at) == (1, thursday + timedelta(days=5))


def test_last_step_delivered_finishes_sequence(
    policy, sandbox, body_template, sequence, django_capture_on_commit_callbacks
):
    state = _sent_cold(sequence)
    ThreadSequenceState.objects.filter(pk=state.pk).update(step=2)
    finished = []
    sequence_finished.connect(lambda sender, thread, **kw: finished.append(thread.pk), weak=False, dispatch_uid="fin")

    with django_capture_on_commit_callbacks(execute=True):
        sequence_service.on_delivered(state.thread.messages.get())

    sequence_finished.disconnect(dispatch_uid="fin")
    state.refresh_from_db()
    assert (state.stop_reason, finished) == (SequenceStopReason.FINISHED, [state.thread.pk])
    assert sequence_service.run_follow_ups() == 0


def _create_follow_up(policy, state: ThreadSequenceState) -> Message:
    clock_service.set_override(policy.channel, THURSDAY_10)
    sequence_service.run_follow_ups(random.Random(0))
    state.refresh_from_db()
    return state.thread.messages.get(sequence_step=1)


def test_C28_failed_follow_up_stops_sequence(policy, sandbox, body_template, sequence):
    state = _sent_cold(sequence)
    follow_up = _create_follow_up(policy, state)

    with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=smtplib.SMTPDataError(554, b"no")):
        send_service.run_send_due()

    state.refresh_from_db()
    follow_up.refresh_from_db()
    assert (follow_up.status, state.stop_reason, state.next_due_at) == ("failed", SequenceStopReason.FAILED, None)
    assert state.stopped_at is not None and sequence_service.run_follow_ups() == 0


def test_C28_rejected_follow_up_rearms(policy, sandbox, body_template, sequence, admin_api):
    state = _sent_cold(sequence)
    MessageTemplate.objects.filter(pk=body_template.pk).update(auto_approve=False)
    follow_up = _create_follow_up(policy, state)
    waiting = (follow_up.status, state.next_due_at)

    review_service.skip(follow_up, user=admin_api.user)

    state.refresh_from_db()
    assert waiting == ("review_required", None)
    assert (state.step, state.stopped_at, state.next_due_at) == (1, None, MONDAY_10 + timedelta(days=5))


def test_C28_edited_follow_up_keeps_step_and_rearms_on_reject(policy, sandbox, body_template, sequence, admin_api):
    state = _sent_cold(sequence)
    MessageTemplate.objects.filter(pk=body_template.pk).update(auto_approve=False)
    follow_up = _create_follow_up(policy, state)

    edited = review_service.edit(follow_up, subject="Hi again", body_text="TEST edited", user=admin_api.user)
    review_service.skip(edited, user=admin_api.user)

    state.refresh_from_db()
    assert (edited.parent_id, edited.sequence_step) == (follow_up.pk, 1)
    assert (state.step, state.stopped_at, state.next_due_at) == (1, None, MONDAY_10 + timedelta(days=5))


def test_follow_up_not_sent_after_reply_or_pause(policy, sandbox, body_template, sequence):
    replied = _sent_cold(sequence)
    paused = _sent_cold(sequence, "anna@example-shop-2.test", "send:2")
    clock_service.set_override(policy.channel, THURSDAY_10)
    created = sequence_service.run_follow_ups(random.Random(0))
    Thread.objects.filter(pk=replied.thread_id).update(status=ThreadStatus.REPLIED)
    sequence_service.pause_sequence(paused.thread)

    send_service.run_send_due()

    outcomes = sorted(Message.objects.filter(sequence_step=1).values_list("status", "failure_detail"))
    assert (created, len(mail.outbox)) == (2, 2)
    assert outcomes == [("skipped", "sequence_not_running"), ("skipped", "thread_replied")]


@pytest.mark.django_db(transaction=True)
def test_concurrent_schedule_creates_one_follow_up(policy, sandbox, body_template, sequence):
    state = _sent_cold(sequence)
    stale = ThreadSequenceState.objects.get(pk=state.pk)

    first = sequence_service.schedule_follow_up(state, random.Random(0))
    second = sequence_service.schedule_follow_up(stale, random.Random(1))

    assert first is not None and second is None
    assert Message.objects.filter(thread=state.thread, sequence_step__isnull=False).count() == 1
