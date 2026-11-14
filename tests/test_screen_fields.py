# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""What the screens no longer guess: the conversations that need a person, the reply of a timeline entry, a mail
written by hand, the label of a thread and the reply titles."""

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from django_communicator.enums import MessageStatus, ReplyIntent, ReplyKind, ReplyMatch, ThreadStatus
from django_communicator.models import Reply, Thread
from django_communicator.services import reply_plan_service
from django_communicator.services.communicate_service import RecipientData
from django_communicator.services.compose_service import compose
from tests.conftest import api_url
from tests.test_inbox_conversations import listed, message, reply, thread

pytestmark = pytest.mark.django_db

FOOTER = "Example Sp. z o.o., KRS 0000000000"


def flagged(owner: Thread, body: str, *, needs_review: bool, kind: str = ReplyKind.REPLY) -> Reply:
    """A reply of the thread, newer than every earlier one; several per thread."""
    Thread.objects.filter(pk=owner.pk).update(status=ThreadStatus.REPLIED, last_message_at=timezone.now())
    return Reply.objects.create(
        channel=owner.channel,
        thread=owner,
        from_email=owner.recipient_email,
        body_text=body,
        kind=kind,
        matched_by=ReplyMatch.HEADER,
        inbound_message_id=f"<{owner.pk}.{Reply.objects.count()}@in.test>",
        received_at=timezone.now(),
        needs_review=needs_review,
    )


def written_by_hand(channel, subject_ref: str = "leads.Company:7"):
    author = get_user_model().objects.create_user(username="author", is_staff=True)
    return compose(
        channel_idx=channel.idx,
        recipient=RecipientData(email="jan@example-shop-1.test", legal_footer=FOOTER),
        subject="Hi",
        body_text="Written by me",
        subject_ref=subject_ref,
        author=author,
    )


def detail(admin_api, owner: Thread) -> dict:
    response = admin_api.get(api_url(f"threads/{owner.pk}/"))
    assert response.status_code == 200, response.content
    return response.json()


# --- needs_review on the conversation list ---


@pytest.fixture
def inbox(channel, policy):
    """A flagged conversation with a draft, an unflagged one, and one whose flagged reply was followed by a plain one."""
    review = thread(channel, "leads.Company:1", email="a@shop.test")
    message(review, MessageStatus.REVIEW_REQUIRED)
    flagged(review, "Who are you?", needs_review=True)
    calm = thread(channel, "leads.Company:2", email="b@shop.test")
    flagged(calm, "Thanks", needs_review=False)
    answered = thread(channel, "leads.Company:3", email="c@shop.test")
    flagged(answered, "Who are you?", needs_review=True)
    flagged(answered, "Fine, send it", needs_review=False)
    return {"review": review, "calm": calm, "answered": answered}


def test_needs_review_lists_only_conversations_whose_newest_human_reply_is_flagged(admin_api, inbox):
    body = listed(admin_api, "needs_review=true")

    assert [row["id"] for row in body["results"]] == [inbox["review"].pk]
    assert body["results"][0]["needs_review"] is True
    assert body["count"] == 1


def test_a_flagged_reply_in_an_older_thread_flags_the_conversation(admin_api, channel, inbox):
    newer = thread(channel, "leads.Company:1", email="other@shop.test")
    message(newer, MessageStatus.SENT)

    assert [row["id"] for row in listed(admin_api, "needs_review=true")["results"]] == [newer.pk]


def test_an_auto_reply_after_a_flagged_one_keeps_the_flag(admin_api, channel, inbox):
    flagged(inbox["review"], "Out of office", needs_review=False, kind=ReplyKind.AUTO)

    assert [row["id"] for row in listed(admin_api, "needs_review=true")["results"]] == [inbox["review"].pk]


def test_counts_follow_needs_review(admin_api, inbox):
    assert listed(admin_api)["counts"] == {"all": 3, "draft": 1, "waiting": 0, "replied": 3}
    assert listed(admin_api, "needs_review=true&state=waiting")["counts"] == {
        "all": 1,
        "draft": 1,
        "waiting": 0,
        "replied": 1,
    }
    assert listed(admin_api, "needs_review=true&state=waiting")["results"] == []


@pytest.mark.parametrize("value", ["false", "1", "True", "yes", ""])
def test_any_other_value_of_needs_review_is_a_400(admin_api, inbox, value):
    assert admin_api.get(api_url(f"conversations/?needs_review={value}")).status_code == 400


# --- the timeline: reply id, composed ---


def test_an_inbound_entry_names_its_reply_and_a_message_has_none(admin_api, channel, policy):
    owner = thread(channel, "leads.Company:4")
    message(owner, MessageStatus.SENT)
    answer = reply(owner, "Call me")

    entries = detail(admin_api, owner)["timeline"]

    assert [(entry["kind"], entry["reply_id"]) for entry in entries] == [("message", None), ("reply", answer.pk)]
    assert [entry["composed"] for entry in entries] == [False, False]


def test_a_mail_written_by_hand_is_composed_on_the_timeline(admin_api, channel, policy):
    written = written_by_hand(channel)
    message(written.thread, MessageStatus.SENT, subject="From a template")

    entries = {entry["message_id"]: entry["composed"] for entry in detail(admin_api, written.thread)["timeline"]}

    assert written.composed is True
    assert entries[written.pk] is True and list(entries.values()).count(True) == 1


def test_the_draft_of_a_row_says_whether_it_was_written_by_hand(admin_api, channel, policy):
    owner = thread(channel, "leads.Company:5")
    message(owner, MessageStatus.REVIEW_REQUIRED, subject="By hand", composed=True)

    draft = listed(admin_api)["results"][0]["draft"]
    thread_draft = admin_api.get(api_url("threads/")).json()["results"][0]["draft"]

    assert draft["composed"] is True and thread_draft["composed"] is True


# --- subject label on the detail ---


def test_the_detail_carries_the_label_and_an_empty_one_when_unknown(admin_api, channel):
    labelled = thread(channel, "leads.Company:6", subject_label="Shop Ltd")
    unlabelled = thread(channel, "bdd:x@shop.test", email="x@shop.test")

    assert detail(admin_api, labelled)["subject_label"] == "Shop Ltd"
    assert detail(admin_api, unlabelled)["subject_label"] == ""


# --- reply titles ---


@pytest.mark.parametrize(
    ("intent", "needs_review", "title"),
    [
        ("", True, "Reply from Shop Ltd — Needs review"),
        ("", False, "Reply from Shop Ltd"),
        (ReplyIntent.WRONG_PERSON, True, "Reply from Shop Ltd — Wrong person"),
        (ReplyIntent.INTERESTED, False, "Reply from Shop Ltd — Interested"),
    ],
)
def test_one_helper_titles_every_reply(intent, needs_review, title):
    answer = Reply(intent=intent, needs_review=needs_review, received_at=timezone.now())

    assert reply_plan_service.reply_title("Shop Ltd", answer) == title
