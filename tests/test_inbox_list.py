# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The inbox list over `threads/`: state filter, counts, activity order and what a row shows."""

from datetime import timedelta

import pytest
from django.utils import timezone

from django_communicator.enums import Direction, MessageStatus, ReplyKind, ReplyMatch, ThreadStatus
from django_communicator.models import Channel, Message, Reply, Thread
from tests.conftest import api_url

pytestmark = pytest.mark.django_db


def thread(channel, email: str, **fields) -> Thread:
    return Thread.objects.create(channel=channel, subject_ref=f"bdd:{email}", recipient_email=email, **fields)


def message(owner: Thread, status: str, subject: str = "Audit", body: str = "Hello", **fields) -> Message:
    return Message.objects.create(
        thread=owner, status=status, direction=Direction.OUT, subject=subject, body_text=body, **fields
    )


def reply(owner: Thread, body: str) -> Reply:
    return Reply.objects.create(
        channel=owner.channel,
        thread=owner,
        from_email=owner.recipient_email,
        subject=f"Re: {owner.recipient_email}",
        body_text=body,
        kind=ReplyKind.REPLY,
        matched_by=ReplyMatch.HEADER,
        inbound_message_id=f"<{owner.pk}@in.test>",
        received_at=timezone.now(),
    )


@pytest.fixture
def inbox(channel, policy):
    """One thread per state: a draft, a waiting mail, a reply, a quiet sent mail — oldest to newest by creation."""
    drafted, waiting, replied, quiet = (thread(channel, f"{name}@shop.test") for name in ("d", "w", "r", "q"))
    message(drafted, MessageStatus.REVIEW_REQUIRED, subject="Draft subject", body="Draft body")
    message(waiting, MessageStatus.SCHEDULED, scheduled_at=timezone.now() + timedelta(days=1))
    message(replied, MessageStatus.SENT)
    reply(replied, "Yes, call me\n\n> Hello")
    message(quiet, MessageStatus.SENT)
    replied_at = timezone.now()  # after every message above: the reply is the latest event
    Thread.objects.filter(pk=replied.pk).update(status=ThreadStatus.REPLIED, last_message_at=replied_at)
    return {"draft": drafted, "waiting": waiting, "replied": replied, "quiet": quiet}


def listed(admin_api, query: str) -> dict:
    return admin_api.get(api_url(f"threads/?{query}")).json()


def test_state_filter_and_counts_ignore_each_other(admin_api, inbox):
    body = listed(admin_api, "state=draft")

    assert [row["id"] for row in body["results"]] == [inbox["draft"].pk]
    assert body["counts"] == {"all": 4, "draft": 1, "waiting": 1, "replied": 1}
    assert [row["id"] for row in listed(admin_api, "state=waiting")["results"]] == [inbox["waiting"].pk]
    assert [row["id"] for row in listed(admin_api, "state=replied")["results"]] == [inbox["replied"].pk]
    assert admin_api.get(api_url("threads/?state=handled")).status_code == 400


def test_activity_sort_puts_the_latest_mail_draft_or_reply_first(admin_api, inbox):
    created = [row["id"] for row in listed(admin_api, "")["results"]]
    active = [row["id"] for row in listed(admin_api, "sort=activity")["results"]]

    assert created == [inbox[key].pk for key in ("quiet", "replied", "waiting", "draft")]
    assert active[0] == inbox["replied"].pk
    message(inbox["draft"], MessageStatus.REVIEW_REQUIRED, subject="Second draft")
    assert listed(admin_api, "sort=activity")["results"][0]["id"] == inbox["draft"].pk


def test_a_row_carries_subject_last_text_draft_and_waiting_slot(admin_api, inbox):
    rows = {row["id"]: row for row in listed(admin_api, "")["results"]}
    draft, waiting, replied = rows[inbox["draft"].pk], rows[inbox["waiting"].pk], rows[inbox["replied"].pk]

    assert draft["subject"] == "Draft subject" and draft["last_text"] == "Draft body"
    assert draft["draft"]["id"] == inbox["draft"].messages.get().pk and draft["waiting"] is None
    assert waiting["waiting"]["status"] == "scheduled" and waiting["waiting"]["next_slot"]
    assert replied["last_text"].startswith("Yes, call me") and replied["draft"] is None
    assert replied["activity_at"] and draft["activity_at"]


def test_the_list_page_costs_a_fixed_number_of_queries(admin_api, inbox, django_assert_max_num_queries):
    """Measured: 12 for 4 rows — one query per row would make it 16."""
    with django_assert_max_num_queries(12):
        assert len(listed(admin_api, "sort=activity")["results"]) == 4


def test_a_bad_channel_locale_keeps_the_list_without_next_slot(admin_api, inbox, channel):
    Channel.objects.filter(pk=channel.pk).update(country="XX")

    response = admin_api.get(api_url("threads/?state=waiting"))

    assert response.status_code == 200
    assert response.json()["results"][0]["waiting"]["next_slot"] is None
