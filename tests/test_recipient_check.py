# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The recipient check of the preflight (address, then the owner of the subject through `recipient_check`) at every
caller, drafts withdrawn when nobody may send them, and rejected drafts on the timeline."""

from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from django_communicator.enums import AddressState, FailureCode, MessageStatus, ReplyKind
from django_communicator.models import AddressStatus, Message, Reply
from django_communicator.services import preflight_service, review_service, send_service, suppression_service
from django_communicator.services.communicate_service import RecipientData, communicate
from django_communicator.signals import recipient_check
from tests.conftest import CHANNEL_IDX, api_url
from tests.factories import make_static

APPROVALS = "/api/communicator/v2/admin/approvals/"
EMAIL = "jan@example-shop-1.test"
REFUSED = "the contact is flagged as the wrong person"
OPTED_OUT = preflight_service.ADDRESS_REFUSALS[suppression_service.SUPPRESSED]


def draft(email: str = EMAIL, ref: str = "x:1") -> Message:
    recipient = RecipientData(email=email, first_name="Jan", legal_footer="Seller.")
    return communicate(channel_idx=CHANNEL_IDX, template_key="cold", recipient=recipient, context={}, subject_ref=ref)


@pytest.fixture
def cold(channel):
    return make_static(channel, key="cold", requires_legal_footer=False)


@pytest.fixture
def owner_refuses():
    """The owner of `x:1` refuses its recipient; every other subject is not its business."""

    def receiver(sender, message, **kwargs):
        return REFUSED if message.thread.subject_ref == "x:1" else None

    recipient_check.connect(receiver, dispatch_uid="test.owner_refuses")
    yield
    recipient_check.disconnect(dispatch_uid="test.owner_refuses")


def suppress(channel, email: str = EMAIL) -> None:
    suppression_service.suppress_email(channel, email, source="manual")


# --- the check ---


def test_a_free_recipient_passes(channel, cold):
    assert preflight_service.problem(draft()) is None


def test_a_suppressed_address_is_refused_with_a_sentence(channel, cold):
    message = draft()
    suppress(channel)

    assert preflight_service.problem(message) == (FailureCode.RECIPIENT, OPTED_OUT)


def test_an_invalid_address_is_refused_with_a_sentence(channel, cold):
    message = draft()
    AddressStatus.objects.create(email=EMAIL, status=AddressState.INVALID)

    assert preflight_service.problem(message) == (FailureCode.RECIPIENT, "the recipient's address is not valid")


def test_the_owner_of_the_subject_refuses_its_recipient(channel, cold, owner_refuses):
    assert preflight_service.problem(draft()) == (FailureCode.RECIPIENT, REFUSED)
    assert preflight_service.problem(draft("ola@example-shop-2.test", ref="other:1")) is None


def test_the_first_sentence_answers(channel, cold, owner_refuses):
    def second(sender, message, **kwargs):
        return "second"

    recipient_check.connect(second, dispatch_uid="test.second")
    try:
        assert preflight_service.problem(draft())[1] == REFUSED
    finally:
        recipient_check.disconnect(dispatch_uid="test.second")


# --- every caller ---


def test_single_approval_is_refused_with_the_sentence(channel, cold, owner_refuses, admin_api):
    message = draft()

    response = admin_api.post(api_url(f"review/{message.pk}/accept/"))

    message.refresh_from_db()
    assert response.status_code == 409 and response.json()["message"] == REFUSED
    assert message.status == MessageStatus.REVIEW_REQUIRED


def test_approvals_row_and_bulk_say_the_same_sentence(channel, cold, owner_refuses, admin_api):
    refused, free = draft(), draft("ola@example-shop-2.test", ref="other:1")

    rows = {row["id"]: row["blocker"] for row in admin_api.get(APPROVALS).json()["results"]}
    body = admin_api.post(APPROVALS + "bulk/", {"action": "approve", "ids": [refused.pk, free.pk]}, format="json")

    assert rows == {refused.pk: REFUSED, free.pk: None}
    assert body.json()["done"] == [free.pk] and body.json()["failed"] == {str(refused.pk): REFUSED}


def test_approvals_page_asks_in_a_constant_number_of_queries_per_row(channel, cold, owner_refuses, admin_api):
    def queries(rows: int) -> int:
        while Message.objects.filter(status=MessageStatus.REVIEW_REQUIRED).count() < rows:
            draft(f"r{rows}-{Message.objects.count()}@example-shop-3.test", ref=f"q:{Message.objects.count()}")
        with CaptureQueriesContext(connection) as captured:
            assert len(admin_api.get(APPROVALS).json()["results"]) == rows
        return len(captured)

    one, two, four = queries(1), queries(2), queries(4)

    assert four - two == 2 * (two - one) and two - one <= 2  # the address check: two queries per row


def test_delivery_fails_a_message_the_owner_refuses(policy, sandbox, channel, cold, owner_refuses):
    message = draft()
    Message.objects.filter(pk=message.pk).update(status=MessageStatus.APPROVED)

    send_service.run_send_due()

    message.refresh_from_db()
    assert (message.status, message.failure_code, message.failure_detail) == (
        MessageStatus.FAILED,
        FailureCode.RECIPIENT,
        REFUSED,
    )
    assert mail.outbox == []


def test_delivery_still_suppresses_a_suppressed_address_first(policy, sandbox, channel, cold, owner_refuses):
    message = draft()
    Message.objects.filter(pk=message.pk).update(status=MessageStatus.APPROVED)
    suppress(channel)

    send_service.run_send_due()

    message.refresh_from_db()
    assert message.status == MessageStatus.SUPPRESSED and mail.outbox == []


# --- withdraw ---


def test_withdraw_rejects_every_waiting_draft_to_the_address_and_nothing_else(channel, cold):
    first, second = draft(), draft(EMAIL.upper(), ref="x:2")
    other, approved = draft("ola@example-shop-2.test", ref="x:3"), draft(ref="x:4")
    Message.objects.filter(pk=approved.pk).update(status=MessageStatus.APPROVED)

    with mock.patch("django_communicator.services.sequence_service.on_follow_up_finished") as rearm:
        withdrawn = review_service.withdraw(channel, EMAIL, reason="the recipient opted out")

    assert sorted(message.pk for message in withdrawn) == [first.pk, second.pk]
    rows = Message.objects.filter(pk__in=[first.pk, second.pk])
    assert {(m.status, m.reject_reason, m.reviewed_by_id) for m in rows} == {
        (MessageStatus.REJECTED, "the recipient opted out", None)
    }
    assert Message.objects.get(pk=other.pk).status == MessageStatus.REVIEW_REQUIRED
    assert Message.objects.get(pk=approved.pk).status == MessageStatus.APPROVED
    rearm.assert_not_called()


# --- the timeline ---


def timeline(admin_api, message: Message) -> list[dict]:
    return admin_api.get(api_url(f"threads/{message.thread_id}/")).json()["timeline"]


def test_a_skipped_draft_is_on_the_timeline_with_who_and_why(channel, cold, admin_api):
    message = draft()
    reviewer = get_user_model().objects.create_user("ewa", first_name="Ewa", last_name="Nowak")
    review_service.skip(message, user=reviewer, reason="not now")

    entry = timeline(admin_api, message)[0]

    assert (entry["status"], entry["rejected_by"], entry["reject_reason"]) == ("rejected", "Ewa Nowak", "not now")


def test_a_withdrawn_draft_has_no_reviewer_and_other_entries_are_empty(channel, cold, admin_api):
    withdrawn = draft()
    review_service.withdraw(channel, EMAIL, reason="the recipient opted out")
    message = draft("ola@example-shop-2.test", ref="x:9")

    [entry] = timeline(admin_api, withdrawn)
    [fresh] = timeline(admin_api, message)

    assert (entry["status"], entry["rejected_by"], entry["reject_reason"]) == (
        "rejected",
        "",
        "the recipient opted out",
    )
    assert (fresh["status"], fresh["rejected_by"], fresh["reject_reason"]) == ("review_required", "", "")


def written_days_ago(message: Message, days: int) -> Message:
    Message.objects.filter(pk=message.pk).update(created_at=timezone.now() - timedelta(days=days))
    message.refresh_from_db()
    return message


def test_a_skipped_draft_is_dated_by_its_skip_after_a_newer_reply(channel, cold, admin_api):
    message = written_days_ago(draft(), 3)
    thread = message.thread
    Reply.objects.create(
        channel=channel,
        thread=thread,
        from_email=EMAIL,
        kind=ReplyKind.REPLY,
        inbound_message_id="<dated@example-shop.test>",
        received_at=timezone.now() - timedelta(days=1),
    )
    review_service.skip(message, user=None, reason="not now")

    entries = timeline(admin_api, message)

    assert [entry["kind"] for entry in entries] == ["reply", "message"]
    message.refresh_from_db()
    assert parse_datetime(entries[1]["at"]) == message.reviewed_at > message.created_at


def test_a_withdrawn_draft_is_dated_by_its_withdrawal(channel, cold, admin_api):
    message = written_days_ago(draft(), 3)
    review_service.withdraw(channel, EMAIL, reason="the recipient opted out")

    [entry] = timeline(admin_api, message)

    message.refresh_from_db()
    assert message.reviewed_by is None and parse_datetime(entry["at"]) == message.reviewed_at > message.created_at


def test_a_waiting_draft_keeps_the_date_it_was_written(channel, cold, admin_api):
    message = written_days_ago(draft(), 3)

    [entry] = timeline(admin_api, message)

    assert parse_datetime(entry["at"]) == message.created_at


def test_an_inbox_row_shows_no_rejected_draft(channel, cold, admin_api):
    message = draft()
    review_service.skip(message, user=None, reason="not now")

    [row] = admin_api.get(api_url("threads/")).json()["results"]

    assert (row["draft"], row["last_text"], row["subject"]) == (None, "", "")  # the timeline still has it
    assert timeline(admin_api, message)[0]["status"] == "rejected"
