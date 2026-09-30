# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The inbox list over `conversations/`: one row per subject_ref, state from any of its threads."""

from datetime import timedelta

import pytest
from django.utils import timezone

from django_communicator.enums import Direction, MessageStatus, ReplyKind, ReplyMatch, ThreadStatus
from django_communicator.models import Message, Reply, Thread
from tests.conftest import api_url

pytestmark = pytest.mark.django_db

COMPANY = "leads.Company:103"


def thread(channel, subject_ref: str, email: str = "owner@shop.test", **fields) -> Thread:
    return Thread.objects.create(channel=channel, subject_ref=subject_ref, recipient_email=email, **fields)


def message(owner: Thread, status: str, subject: str = "Audit", body: str = "Hello", **fields) -> Message:
    return Message.objects.create(
        thread=owner, status=status, direction=Direction.OUT, subject=subject, body_text=body, **fields
    )


def reply(owner: Thread, body: str) -> Reply:
    Thread.objects.filter(pk=owner.pk).update(status=ThreadStatus.REPLIED, last_message_at=timezone.now())
    return Reply.objects.create(
        channel=owner.channel,
        thread=owner,
        from_email=owner.recipient_email,
        subject=f"Re: {owner.subject_ref}",
        body_text=body,
        kind=ReplyKind.REPLY,
        matched_by=ReplyMatch.HEADER,
        inbound_message_id=f"<{owner.pk}@in.test>",
        received_at=timezone.now(),
    )


def listed(admin_api, query: str = "") -> dict:
    response = admin_api.get(api_url(f"conversations/?{query}"))
    assert response.status_code == 200, response.content
    return response.json()


@pytest.fixture
def company(channel, policy):
    """Company 103: an old thread with a reply and a draft to review, then a newer outreach that was sent."""
    old = thread(channel, COMPANY)
    message(old, MessageStatus.SENT, subject="First audit")
    old_draft = message(old, MessageStatus.REVIEW_REQUIRED, subject="Answer draft", body="Thanks")
    reply(old, "Call me\n\n> Hello")
    new = thread(channel, COMPANY, email="sales@shop.test")
    message(new, MessageStatus.SENT, subject="Second audit", body="Hello again")
    return {"old": old, "new": new, "draft": old_draft}


def test_several_threads_of_a_subject_collapse_to_one_row_of_the_newest_thread(admin_api, company):
    body = listed(admin_api)

    assert body["count"] == 1
    row = body["results"][0]
    assert row["id"] == company["new"].pk and row["recipient_email"] == "sales@shop.test"
    assert row["subject"] == "Second audit" and row["thread_count"] == 2
    assert row["replied"] is True and row["status"] == "open"
    assert row["last_text"] == "Hello again"


def test_a_draft_in_an_older_thread_lists_the_conversation_under_drafts_with_that_draft(admin_api, company):
    rows = listed(admin_api, "state=draft")["results"]

    assert [row["id"] for row in rows] == [company["new"].pk]
    assert rows[0]["draft"] == {
        "id": company["draft"].pk,
        "subject": "Answer draft",
        "recipient_email": "owner@shop.test",
    }
    assert rows[0]["recipient_email"] == "sales@shop.test"  # the newest thread's; the draft names its own
    assert [row["id"] for row in listed(admin_api, "state=replied")["results"]] == [company["new"].pk]
    assert listed(admin_api, "state=waiting")["results"] == []


def test_a_waiting_mail_in_an_older_thread_is_the_rows_waiting_entry(admin_api, company):
    waiting = message(company["old"], MessageStatus.SCHEDULED, scheduled_at=timezone.now() + timedelta(days=1))

    rows = listed(admin_api, "state=waiting")["results"]

    assert [row["id"] for row in rows] == [company["new"].pk]
    assert rows[0]["waiting"]["id"] == waiting.pk and rows[0]["waiting"]["next_slot"]


def test_counts_count_conversations_not_threads(admin_api, channel, company):
    other = thread(channel, "bdd:jan@shop.test", email="jan@shop.test")
    message(other, MessageStatus.SENT)

    assert listed(admin_api, "state=waiting")["counts"] == {"all": 2, "draft": 1, "waiting": 0, "replied": 1}
    assert admin_api.get(api_url("conversations/?state=handled")).status_code == 400


def test_a_non_company_subject_ref_is_its_own_conversation(admin_api, channel, company):
    first = thread(channel, "bdd:a@shop.test", email="a@shop.test")
    second = thread(channel, "bdd:b@shop.test", email="b@shop.test")

    rows = {row["id"]: row for row in listed(admin_api)["results"]}

    assert set(rows) == {company["new"].pk, first.pk, second.pk}
    assert rows[first.pk]["thread_count"] == 1 and rows[first.pk]["subject_ref"] == "bdd:a@shop.test"


def test_conversations_are_ordered_by_their_latest_activity_in_any_thread(admin_api, channel, company):
    later = thread(channel, "bdd:later@shop.test", email="later@shop.test")
    message(later, MessageStatus.SENT)
    assert [row["id"] for row in listed(admin_api)["results"]] == [later.pk, company["new"].pk]

    message(company["old"], MessageStatus.REVIEW_REQUIRED, subject="Newest draft")  # activity in the older thread

    assert [row["id"] for row in listed(admin_api)["results"]] == [company["new"].pk, later.pk]


def test_paging_runs_over_conversations(admin_api, channel, company):
    for index in range(3):
        message(thread(channel, f"bdd:{index}@shop.test", email=f"{index}@shop.test"), MessageStatus.SENT)

    first, second = listed(admin_api, "page_size=2"), listed(admin_api, "page_size=2&page=2")

    assert first["count"] == 4 and first["next"] and not second["next"]
    ids = [row["id"] for row in first["results"] + second["results"]]
    assert len(ids) == len(set(ids)) == 4 and company["new"].pk in ids


def test_the_conversation_page_costs_a_fixed_number_of_queries(
    admin_api, channel, company, django_assert_max_num_queries
):
    """Measured: 13 for 6 conversations of 7 threads — one query per row would make it 19 or more."""
    for index in range(5):
        message(thread(channel, f"bdd:{index}@shop.test", email=f"{index}@shop.test"), MessageStatus.SENT)
    with django_assert_max_num_queries(13):
        assert len(listed(admin_api)["results"]) == 6


def test_the_plain_thread_list_still_lists_every_thread(admin_api, company):
    body = admin_api.get(api_url("threads/?sort=activity")).json()

    assert body["count"] == 2 and body["counts"]["all"] == 2
