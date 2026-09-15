# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""FIX-16 item 2: failed AI drafts are retried after a transient toolbox outage — bounded, never auto-sent."""

import httpx
import pytest
from django.db import connection
from django_notifications.models import Notification
from django_utils.toolbox.testing import error_response

from django_communicator.enums import FailureCode, MessageStatus
from django_communicator.models import Message
from django_communicator.services import draft_retry_service, suppression_service
from django_communicator.services.communicate_service import communicate
from django_communicator.signals import draft_retry_requested
from tests.conftest import CHANNEL_IDX, DRAFT, api_url, draft_response


def _failed_draft(toolbox, recipient, context, answer) -> Message:
    route = toolbox["complete"]
    route.mock(side_effect=answer) if isinstance(answer, Exception) else route.mock(return_value=answer)
    message = communicate(
        channel_idx=CHANNEL_IDX, template_key="lead.cold.shop", recipient=recipient, context=context, subject_ref="r:1"
    )
    route.mock(side_effect=None, return_value=draft_response())
    return message


@pytest.fixture
def notifications_channel(db):
    from django_notifications.models import Channel as NotificationsChannel

    return NotificationsChannel.objects.create(idx=CHANNEL_IDX, label="Notifications")


@pytest.fixture
def outage_draft(ai_template, toolbox, recipient, context) -> Message:
    return _failed_draft(toolbox, recipient, context, httpx.ConnectError("down"))


@pytest.mark.parametrize(
    "answer",
    [httpx.ConnectError("down"), httpx.ReadTimeout("slow"), httpx.Response(502), httpx.Response(503)],
)
def test_item2_transient_failure_is_recovered_to_review_required(ai_template, toolbox, recipient, context, answer):
    message = _failed_draft(toolbox, recipient, context, answer)
    assert (message.status, message.failure_code) == (MessageStatus.FAILED, FailureCode.UPSTREAM)

    counts = draft_retry_service.retry_failed_drafts()

    message.refresh_from_db()
    assert counts == {"recovered": 1, "failed": 0}
    assert (message.status, message.subject, message.draft_retries) == (
        MessageStatus.REVIEW_REQUIRED,
        DRAFT["subject"],
        1,
    )
    assert message.failure_code == "" and message.failure_detail.endswith("\nretry 1: recovered")
    assert message.reviewed_by is None and message.sent_at is None


@pytest.mark.parametrize(
    "answer",
    [
        error_response(402, "BUDGET_EXCEEDED"),
        error_response(403, "MODEL_NOT_ALLOWED"),
        error_response(422, "SCHEMA_INVALID"),
        error_response(401, "AUTHENTICATION_REQUIRED"),
        draft_response({"ok": True}),
    ],
)
def test_item2_permanent_failure_is_never_retried(ai_template, toolbox, recipient, context, answer):
    message = _failed_draft(toolbox, recipient, context, answer)

    assert draft_retry_service.retry_failed_drafts() == {"recovered": 0, "failed": 0}
    message.refresh_from_db()
    assert (message.status, message.draft_retries, message.rendered_prompt) == (MessageStatus.FAILED, 0, "")
    assert toolbox["complete"].call_count == 1


def test_item2_unreachable_toolbox_is_a_no_op(outage_draft, toolbox):
    toolbox["models"].mock(side_effect=httpx.ConnectError("still down"))

    assert draft_retry_service.retry_failed_drafts() == {"recovered": 0, "failed": 0}
    outage_draft.refresh_from_db()
    assert (outage_draft.status, outage_draft.draft_retries) == (MessageStatus.FAILED, 0)
    assert toolbox["complete"].call_count == 1


def test_item2_attempts_are_capped_and_the_last_one_alerts(outage_draft, toolbox, notifications_channel):
    toolbox["complete"].mock(return_value=httpx.Response(500))

    results = [draft_retry_service.retry_failed_drafts() for _ in range(4)]

    outage_draft.refresh_from_db()
    assert results == [{"recovered": 0, "failed": 1}] * 3 + [{"recovered": 0, "failed": 0}]
    assert (outage_draft.status, outage_draft.draft_retries) == (MessageStatus.FAILED, 3)
    assert outage_draft.failure_detail.count("\nretry ") == 3
    assert toolbox["complete"].call_count == 4
    assert Notification.objects.count() == 1


def test_item2_permanent_error_on_retry_stops_the_retries(outage_draft, toolbox, notifications_channel):
    toolbox["complete"].mock(return_value=error_response(402, "BUDGET_EXCEEDED"))

    assert draft_retry_service.retry_failed_drafts() == {"recovered": 0, "failed": 1}
    assert draft_retry_service.retry_failed_drafts() == {"recovered": 0, "failed": 0}
    outage_draft.refresh_from_db()
    assert (outage_draft.failure_code, outage_draft.draft_retries) == (FailureCode.BUDGET, 1)
    assert Notification.objects.get().severity == "high"


def test_item2_claimed_retry_is_not_repeated(outage_draft, toolbox):
    stale = Message.objects.get(pk=outage_draft.pk)
    draft_retry_service.retry(outage_draft)

    assert draft_retry_service.retry(stale) is None
    assert toolbox["complete"].call_count == 2


def test_item2_suppressed_recipient_is_skipped(outage_draft, ai_template, recipient):
    suppression_service.create_suppression(ai_template.channel, kind="email", value=recipient.email)

    assert draft_retry_service.candidates() == []


def test_item2_draft_replaced_by_a_newer_message_is_skipped(outage_draft, recipient, context):
    newer = communicate(
        channel_idx=CHANNEL_IDX, template_key="lead.cold.shop", recipient=recipient, context=context, subject_ref="r:1"
    )

    assert newer.thread_id == outage_draft.thread_id
    assert draft_retry_service.candidates() == []


@pytest.mark.django_db(transaction=True)
def test_item2_no_toolbox_call_inside_a_transaction(outage_draft, toolbox):
    def answer(request):
        assert not connection.in_atomic_block
        return draft_response()

    toolbox["complete"].mock(side_effect=answer)

    assert draft_retry_service.retry_failed_drafts() == {"recovered": 1, "failed": 0}


def test_item2_dev_retry_endpoint_runs_the_task_body(outage_draft, admin_api, once_backend):
    response = admin_api.post(api_url("test/retry-drafts/"))

    assert (response.status_code, response.json()) == (200, {"recovered": 1, "failed": 0})


@pytest.mark.parametrize(("debug", "switch", "expected"), [(False, False, 404), (False, True, 200), (True, False, 200)])
def test_item3_outage_endpoint_exists_only_where_the_switch_is_allowed(
    channel, admin_api, settings, debug, switch, expected
):
    settings.DEBUG, settings.AI_TOOLBOX_TEST_SWITCH = debug, switch

    response = admin_api.post(api_url("test/toolbox-outage/"), {"down": True}, format="json")

    assert response.status_code == expected


def test_item3_outage_endpoint_fails_drafts_then_recovery_retries_them(
    ai_template, admin_api, settings, toolbox, recipient, context
):
    settings.AI_TOOLBOX_TEST_SWITCH = True
    assert admin_api.post(api_url("test/toolbox-outage/"), {"down": True}, format="json").json() == {"down": True}
    message = communicate(
        channel_idx=CHANNEL_IDX, template_key="lead.cold.shop", recipient=recipient, context=context, subject_ref="r:9"
    )
    assert draft_retry_service.retry_failed_drafts() == {"recovered": 0, "failed": 0}

    assert admin_api.post(api_url("test/toolbox-outage/"), {"down": False}, format="json").json() == {"down": False}

    assert draft_retry_service.retry_failed_drafts() == {"recovered": 1, "failed": 0}
    message.refresh_from_db()
    assert message.status == MessageStatus.REVIEW_REQUIRED
    assert toolbox["complete"].call_count == 1


# --- FIX-16a ---


@pytest.fixture
def subject_answer():
    answers = []

    def receiver(sender, message, **kwargs):
        answer = answers[-1] if answers else None
        if isinstance(answer, Exception):
            raise answer
        return answer

    draft_retry_requested.connect(receiver, dispatch_uid="tests.subject_answer")
    yield answers
    draft_retry_requested.disconnect(dispatch_uid="tests.subject_answer")


def test_item2_subject_blocked_during_outage_does_not_revive_the_draft(outage_draft, toolbox, subject_answer):
    subject_answer.append("do_not_contact")

    assert draft_retry_service.retry_failed_drafts() == {"recovered": 0, "failed": 0}
    subject_answer.append(None)
    assert draft_retry_service.retry_failed_drafts() == {"recovered": 0, "failed": 0}

    outage_draft.refresh_from_db()
    assert (outage_draft.status, outage_draft.draft_retries) == (MessageStatus.FAILED, 0)
    assert outage_draft.failure_detail.endswith("\nretry: blocked_by_subject do_not_contact")
    assert toolbox["complete"].call_count == 1


def test_item2_failing_subject_check_skips_the_run_only(outage_draft, toolbox, subject_answer):
    subject_answer.append(RuntimeError("leads down"))
    assert draft_retry_service.retry_failed_drafts() == {"recovered": 0, "failed": 0}

    subject_answer.append(None)
    assert draft_retry_service.retry_failed_drafts() == {"recovered": 1, "failed": 0}


def test_item4_outage_endpoint_rejects_unknown_keys(channel, admin_api, settings):
    settings.AI_TOOLBOX_TEST_SWITCH = True

    response = admin_api.post(api_url("test/toolbox-outage/"), {"down": True, "minutes": 5}, format="json")

    assert response.status_code == 400
