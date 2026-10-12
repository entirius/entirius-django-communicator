# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""`AI_TOOLBOX_ENABLED = False`: AI drafts and rewrites are refused before any record, nothing fails or alerts."""

import random
from datetime import timedelta

import httpx
import pytest
from django_notifications.models import Notification
from django_utils.toolbox import ToolboxDisabledError

from django_communicator.enums import MessageStatus
from django_communicator.models import Message, Sequence, SequenceStep, TextPool, Thread
from django_communicator.services import (
    clock_service,
    draft_retry_service,
    review_service,
    send_service,
    sequence_service,
    suppression_service,
)
from django_communicator.services.communicate_service import communicate
from tests.conftest import CHANNEL_IDX, MONDAY_10, api_url, approved_message
from tests.factories import make_template

DISABLED = (503, "AI_TOOLBOX_DISABLED")
DEV_BODY = {
    "template_key": "lead.cold.shop",
    "recipient": {"email": "jan@example-shop-1.test", "first_name": "Jan", "language": "pl"},
    "context": {"company_name": "S"},
    "subject_ref": "r:2",
}


def _communicate(recipient, context, key="lead.cold.shop", **kwargs) -> Message:
    return communicate(
        channel_idx=CHANNEL_IDX, template_key=key, recipient=recipient, context=context, subject_ref="bdd:42", **kwargs
    )


def _answer(response) -> tuple[int, str]:
    return response.status_code, response.json().get("error", "")


@pytest.fixture
def notifications_channel(db):
    from django_notifications.models import Channel as NotificationsChannel

    return NotificationsChannel.objects.create(idx=CHANNEL_IDX, label="Notifications")


@pytest.fixture
def draft(ai_template, toolbox, recipient, context) -> Message:
    return _communicate(recipient, context)


def test_disabled_ai_communicate_raises_and_writes_nothing(
    ai_template, toolbox, recipient, context, notifications_channel, settings
):
    settings.AI_TOOLBOX_ENABLED = False

    with pytest.raises(ToolboxDisabledError):
        _communicate(recipient, context)

    assert not Thread.objects.exists() and not Message.objects.exists() and not Notification.objects.exists()
    assert toolbox["complete"].call_count == 0


def test_disabled_static_template_is_still_approved(static_template, toolbox, recipient, context, settings):
    settings.AI_TOOLBOX_ENABLED = False

    message = _communicate(recipient, context, key="followup", requires_review=False)

    assert message.status == MessageStatus.APPROVED


def test_disabled_suppressed_recipient_is_still_suppressed(ai_template, toolbox, recipient, context, settings):
    settings.AI_TOOLBOX_ENABLED = False
    suppression_service.suppress_email(ai_template.channel, recipient.email, reason="test")

    assert _communicate(recipient, context).status == MessageStatus.SUPPRESSED


def test_unconfigured_ai_communicate_still_writes_a_failed_draft(
    ai_template, toolbox, recipient, context, notifications_channel, settings
):
    settings.AI_TOOLBOX_API_KEY = ""

    message = _communicate(recipient, context)

    assert message.status == MessageStatus.FAILED and Notification.objects.count() == 1


def test_disabled_rewrite_answers_503_and_creates_no_version(draft, toolbox, admin_api, settings):
    settings.AI_TOOLBOX_ENABLED = False

    response = admin_api.post(api_url(f"review/{draft.pk}/rewrite/"), {"notes": "Shorter."}, format="json")
    with pytest.raises(ToolboxDisabledError):
        review_service.rewrite(draft, notes="Shorter.", automated=True)

    draft.refresh_from_db()
    assert _answer(response) == DISABLED
    assert (Message.objects.count(), draft.status, draft.automated_rewrites) == (1, "review_required", 0)
    assert toolbox["complete"].call_count == 1


def test_disabled_rewrite_of_a_static_message_is_still_refused(
    static_template, recipient, context, admin_api, settings
):
    settings.AI_TOOLBOX_ENABLED = False
    message = _communicate(recipient, context, key="followup")

    response = admin_api.post(api_url(f"review/{message.pk}/rewrite/"), {"notes": "x"}, format="json")

    assert response.status_code == 409


def test_unconfigured_rewrite_still_returns_a_failed_version(draft, toolbox, admin_api, settings):
    settings.AI_TOOLBOX_API_KEY = ""

    response = admin_api.post(api_url(f"review/{draft.pk}/rewrite/"), {"notes": "Shorter."}, format="json")

    assert (response.status_code, response.json()["status"]) == (201, "failed")


def test_configured_rewrite_still_creates_a_version(draft, toolbox, admin_api):
    response = admin_api.post(api_url(f"review/{draft.pk}/rewrite/"), {"notes": "Shorter."}, format="json")

    assert (response.status_code, response.json()["status"]) == (201, "review_required")


def test_disabled_dev_communicate_answers_503(ai_template, toolbox, admin_api, settings):
    settings.AI_TOOLBOX_ENABLED = False

    response = admin_api.post(api_url("test/communicate/"), DEV_BODY, format="json")

    assert _answer(response) == DISABLED and not Message.objects.exists()


def test_unconfigured_dev_communicate_still_returns_a_failed_draft(ai_template, toolbox, admin_api, settings):
    settings.AI_TOOLBOX_API_KEY = ""

    response = admin_api.post(api_url("test/communicate/"), DEV_BODY, format="json")

    assert (response.status_code, response.json()["status"]) == (201, "failed")


def test_disabled_test_generate_and_models_answer_503(ai_template, toolbox, admin_api, settings):
    settings.AI_TOOLBOX_ENABLED = False
    body = {"context": {"first_name": "Jan", "company_name": "Shop"}}

    generated = admin_api.post(api_url(f"templates/{ai_template.pk}/test-generate/"), body, format="json")
    models = admin_api.get(api_url("models/"))

    assert (_answer(generated), _answer(models)) == (DISABLED, DISABLED)
    assert generated.json()["message"] == "AI features are switched off on this instance."


def test_unconfigured_test_generate_and_models_keep_their_code(ai_template, toolbox, admin_api, settings):
    settings.AI_TOOLBOX_API_KEY = ""
    body = {"context": {"first_name": "Jan", "company_name": "Shop"}}

    generated = admin_api.post(api_url(f"templates/{ai_template.pk}/test-generate/"), body, format="json")
    models = admin_api.get(api_url("models/"))

    assert _answer(generated) == _answer(models) == (503, "AI_TOOLBOX_NOT_CONFIGURED")


def test_disabled_draft_retry_is_a_no_op(ai_template, toolbox, recipient, context, settings):
    toolbox["complete"].mock(side_effect=httpx.ConnectError("down"))
    failed = _communicate(recipient, context)
    settings.AI_TOOLBOX_ENABLED = False

    assert draft_retry_service.retry_failed_drafts() == {"recovered": 0, "failed": 0}
    failed.refresh_from_db()
    assert (failed.status, failed.draft_retries) == (MessageStatus.FAILED, 0)
    assert (toolbox["complete"].call_count, toolbox["models"].call_count) == (1, 0)


@pytest.fixture
def ai_sequence(channel, policy, sandbox, static_template):
    """A sent cold mail with a running sequence whose step drafts through the toolbox; the follow-up is due."""
    make_template(channel, key="followup.ai", body="You follow up.\n=== USER ===\nRemind: {body}")
    sequence = Sequence.objects.create(channel=channel, key="followup")
    SequenceStep.objects.create(sequence=sequence, number=1, days_after_previous=3, template_key="followup.ai")
    TextPool.objects.create(sequence=sequence, body="TEST follow-up")
    cold = approved_message()
    send_service.run_send_due()
    state = sequence_service.start_sequence(cold.thread, sequence)
    clock_service.set_override(channel, MONDAY_10 + timedelta(days=3))
    return state


def test_disabled_due_ai_follow_up_stays_due_and_runs_after_enabling(ai_sequence, toolbox, settings):
    settings.AI_TOOLBOX_ENABLED = False
    due = ai_sequence.next_due_at

    assert sequence_service.run_follow_ups(random.Random(0)) == 0
    ai_sequence.refresh_from_db()
    assert (ai_sequence.step, ai_sequence.next_due_at, ai_sequence.stopped_at) == (0, due, None)
    assert not Message.objects.filter(status=MessageStatus.FAILED).exists()

    settings.AI_TOOLBOX_ENABLED = True
    assert sequence_service.run_follow_ups(random.Random(0)) == 1
    follow_up = ai_sequence.thread.messages.get(sequence_step=1)
    assert (follow_up.status, toolbox["complete"].call_count) == (MessageStatus.REVIEW_REQUIRED, 1)
