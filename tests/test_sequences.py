# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import logging
import random
from datetime import timedelta

import pytest
from django.core import mail

from django_communicator.enums import SequenceStopReason
from django_communicator.models import Sequence, SequenceStep, TextPool, ThreadPoolUsage, ThreadSequenceState
from django_communicator.services import clock_service, counter_service, send_service, sequence_service
from django_communicator.signals import sequence_finished
from tests.conftest import MONDAY_10, approved_message


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


def _sent_cold(sequence) -> ThreadSequenceState:
    cold = approved_message(footer="Footer.", context={"body": "Cold."})
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
