# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Our mail names the address it leaves from: timeline entries and a row's draft, mailbox or channel sender."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from django_communicator.enums import MessageStatus
from tests.conftest import api_url
from tests.factories import make_mailbox
from tests.test_inbox_conversations import listed, message, reply, thread

pytestmark = pytest.mark.django_db

CHANNEL_SENDER = "outreach@mail.example.test"  # DEFAULT_FROM_EMAIL of the channel entry in tests/settings.py
MAILBOX = "anna@mail.example.test"


def detail(admin_api, owner) -> dict:
    response = admin_api.get(api_url(f"threads/{owner.pk}/"))
    assert response.status_code == 200, response.content
    return response.json()


def senders(admin_api, owner) -> list[tuple[str, str]]:
    return [(entry["direction"], entry["from_email"]) for entry in detail(admin_api, owner)["timeline"]]


def test_without_a_mailbox_our_mail_names_the_channel_sender(admin_api, channel, policy):
    owner = thread(channel, "lead:1")
    message(owner, MessageStatus.SENT)
    reply(owner, "Thanks")
    message(owner, MessageStatus.REVIEW_REQUIRED)

    assert senders(admin_api, owner) == [("out", CHANNEL_SENDER), ("in", "owner@shop.test"), ("out", CHANNEL_SENDER)]


def test_with_a_mailbox_our_mail_names_the_bare_mailbox_address(admin_api, channel, policy):
    owner = thread(channel, "lead:1", mailbox=make_mailbox(channel, MAILBOX, display_name="Anna Nowak"))
    message(owner, MessageStatus.SENT)
    reply(owner, "Thanks")

    assert senders(admin_api, owner) == [("out", MAILBOX), ("in", "owner@shop.test")]


def test_a_rows_draft_names_its_sender(admin_api, channel, policy):
    mailbox = make_mailbox(channel, MAILBOX)
    message(thread(channel, "lead:1", mailbox=mailbox), MessageStatus.REVIEW_REQUIRED)
    message(thread(channel, "lead:2", "other@shop.test"), MessageStatus.REVIEW_REQUIRED)

    conversations = {row["subject_ref"]: row["draft"]["from_email"] for row in listed(admin_api)["results"]}
    threads = admin_api.get(api_url("threads/")).json()["results"]

    assert conversations == {"lead:1": MAILBOX, "lead:2": CHANNEL_SENDER}
    assert {row["subject_ref"]: row["draft"]["from_email"] for row in threads} == conversations


def test_the_sender_costs_no_query_per_entry(admin_api, channel, policy):
    owner = thread(channel, "lead:1", mailbox=make_mailbox(channel, MAILBOX))
    message(owner, MessageStatus.SENT)

    def queries() -> int:
        with CaptureQueriesContext(connection) as captured:
            detail(admin_api, owner)
        return len(captured)

    one = queries()
    for _ in range(3):
        message(owner, MessageStatus.SENT)
    assert queries() == one
