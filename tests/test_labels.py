# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Words for people: the thread's subject label, reply notification titles, one bell entry per reply, the user
named in the pause log."""

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from django_communicator.enums import PauseAction, ReplyIntent
from django_communicator.models import MailboxPauseLog, Thread
from django_communicator.services import reply_plan_service
from django_communicator.services.communicate_service import RecipientData, communicate
from django_communicator.services.compose_service import compose
from django_communicator.signals import REPLY_ALERTED, reply_received
from tests.conftest import api_url
from tests.factories import make_mailbox, make_static
from tests.test_reply_classes import EMAIL, given, ingest_given, sent

FOOTER = "Example Sp. z o.o., KRS 0000000000"
APPROVALS = "/api/communicator/v2/admin/approvals/"


def _communicate(channel, label: str = "", email: str = "jan@example-shop-1.test"):
    recipient = RecipientData(email=email, first_name="Jan")
    return communicate(
        channel_idx=channel.idx,
        template_key="cold",
        recipient=recipient,
        context={},
        subject_ref="leads.Company:7",
        subject_label=label,
    )


@pytest.fixture
def cold(channel):
    return make_static(channel, key="cold", requires_legal_footer=False)


# --- subject label ---


def test_subject_label_is_stored_replaced_and_kept_when_blank(channel, cold):
    thread = _communicate(channel, "Shop Ltd").thread
    assert Thread.objects.get(pk=thread.pk).subject_label == "Shop Ltd"

    assert _communicate(channel, "Shop Ltd (renamed)").thread_id == thread.pk
    assert Thread.objects.get(pk=thread.pk).subject_label == "Shop Ltd (renamed)"

    _communicate(channel, "")
    assert Thread.objects.get(pk=thread.pk).subject_label == "Shop Ltd (renamed)"


def test_compose_stores_the_label(channel, policy):
    author = get_user_model().objects.create_user(username="author", is_staff=True)
    message = compose(
        channel_idx=channel.idx,
        recipient=RecipientData(email=EMAIL, legal_footer=FOOTER),
        subject="Hi",
        body_text="Hello",
        subject_ref="leads.Company:7",
        author=author,
        subject_label="Shop Ltd",
    )
    assert Thread.objects.get(pk=message.thread_id).subject_label == "Shop Ltd"


def test_dev_endpoints_take_the_label(channel, cold, policy, admin_api):
    recipient = {"email": EMAIL, "first_name": "Jan", "legal_footer": FOOTER}
    drafted = {"template_key": "cold", "recipient": recipient, "subject_ref": "r:1", "subject_label": "Shop A"}
    written = {"recipient": recipient, "subject": "Hi", "body_text": "Hello", "subject_ref": "r:2"}

    first = admin_api.post(api_url("test/communicate/"), drafted, format="json")
    second = admin_api.post(api_url("test/compose/"), {**written, "subject_label": "Shop B"}, format="json")

    assert (first.status_code, second.status_code) == (201, 201)
    assert sorted(Thread.objects.values_list("subject_label", flat=True)) == ["Shop A", "Shop B"]


# --- approval row ---


def test_approval_row_carries_the_label_and_search_matches_it(channel, cold, admin_api):
    labelled = _communicate(channel, "Northwind Traders")
    _communicate(channel, "", email="ola@example-shop-2.test")

    rows = admin_api.get(APPROVALS).json()["results"]
    found = admin_api.get(APPROVALS, {"search": "northwind"}).json()["results"]

    assert sorted(row["subject_label"] for row in rows) == ["", "Northwind Traders"]
    assert [row["id"] for row in found] == [labelled.pk]


# --- reply titles ---


@pytest.mark.parametrize(
    ("intent", "title"),
    [
        (ReplyIntent.INTERESTED, f"Reply from {EMAIL} — Interested"),
        (ReplyIntent.NOT_NOW, f"Reply from {EMAIL} — Not now"),
        (ReplyIntent.NOT_INTERESTED, f"Reply from {EMAIL} — Not interested"),
        (ReplyIntent.OUT_OF_OFFICE, f"Reply from {EMAIL} — Out of office"),
        (ReplyIntent.WRONG_PERSON, f"Reply from {EMAIL} — Wrong person"),
        (ReplyIntent.UNCLEAR, f"Reply from {EMAIL} — Unclear"),
        (ReplyIntent.UNSUBSCRIBE, f"Possible opt-out — {EMAIL}"),
        (None, f"Reply from {EMAIL}"),
    ],
)
def test_reply_titles_are_words(channel, intent, title):
    _, notify = ingest_given(sent(channel), "Dziękuję.", given(intent) if intent else None)

    assert notify.call_count == 1 and notify.call_args.kwargs["title"] == title
    assert not set("()[]_") & set(title)


def test_every_class_has_words():
    labels = {intent: reply_plan_service.reply_type_label(intent) for intent in ReplyIntent.values}
    assert labels == {
        "interested": "Interested",
        "not_now": "Not now",
        "not_interested": "Not interested",
        "out_of_office": "Out of office",
        "wrong_person": "Wrong person",
        "unsubscribe": "Unsubscribe",
        "unclear": "Unclear",
    }
    assert reply_plan_service.reply_type_label("") == ""


# --- one bell entry per reply ---


@pytest.fixture
def connected():
    receivers = []

    def connect(receiver):
        receivers.append(receiver)
        reply_received.connect(receiver, weak=False)

    yield connect
    for receiver in receivers:
        reply_received.disconnect(receiver)


def test_no_communicator_alert_when_a_receiver_alerted(channel, connected):
    connected(lambda **kwargs: REPLY_ALERTED)
    _, notify = ingest_given(sent(channel), "Chętnie.", given(ReplyIntent.INTERESTED))
    assert notify.call_count == 0


def test_communicator_alerts_when_no_receiver_alerted(channel, connected):
    connected(lambda **kwargs: None)
    _, notify = ingest_given(sent(channel), "Chętnie.", given(ReplyIntent.INTERESTED))
    assert notify.call_count == 1


def test_a_failing_receiver_leaves_communicator_alert_and_other_receivers(channel, connected):
    seen = []

    def failing(**kwargs):
        raise RuntimeError("receiver down")

    connected(failing)
    connected(lambda reply, **kwargs: seen.append(reply.pk))
    reply, notify = ingest_given(sent(channel), "Chętnie.", given(ReplyIntent.INTERESTED))
    assert notify.call_count == 1 and seen == [reply.pk]


# --- pause log ---


def _log(channel, mailbox, user=None):
    trigger = "manual" if user else "bounce"
    return MailboxPauseLog.objects.create(
        channel=channel, mailbox=mailbox, action=PauseAction.PAUSE, trigger=trigger, user=user
    )


def _page_queries(admin_api) -> tuple[list[dict], int]:
    with CaptureQueriesContext(connection) as queries:
        rows = admin_api.get(api_url("health/log/")).json()["results"]
    return rows, len(queries.captured_queries)


def test_pause_log_names_the_user_without_a_query_per_row(channel, admin_api):
    users = get_user_model().objects
    mailbox = make_mailbox(channel)
    _log(channel, mailbox, users.create_user(username="anna", first_name="Anna", last_name="Nowak"))
    _, one_row = _page_queries(admin_api)
    _log(channel, mailbox, users.create_user(username="piotr"))
    _log(channel, mailbox)

    rows, three_rows = _page_queries(admin_api)

    assert [row["user_name"] for row in rows] == ["", "piotr", "Anna Nowak"]
    assert three_rows == one_row
