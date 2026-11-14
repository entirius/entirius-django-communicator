# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The golden set of Polish and English replies (`tests/fixtures/reply_golden.json`) against the deterministic fake
toolbox (`tests/fake_toolbox.py`), end to end with the default plans. It tests the communicator path, not how well a
real model classifies."""

import json
from collections import Counter
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

import pytest

from django_communicator.enums import ReplyIntent, ReplyKind, SequenceStopReason, ThreadStatus
from django_communicator.models import Reply, ReplyClassProfile, Suppression, Thread
from django_communicator.services import clock_service, mail_parser, reply_class_service
from django_communicator.services.inbound_service import ingest
from tests.fake_toolbox import class_toolbox
from tests.test_inbound import NOTIFY, OUR_ID, make_sent, state_of

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "reply_golden.json").read_text())
IDS = [f"{entry['intent']}-{entry['lang']}-{n}" for n, entry in enumerate(GOLDEN)]
EMAIL = "owner@example-shop-1.test"

# What each class does with the default plan: (reply kind, sequence stop reason, alert severity, needs_review)
EXPECTED = {
    ReplyIntent.INTERESTED: (ReplyKind.REPLY, SequenceStopReason.REPLIED, "high", False),
    ReplyIntent.NOT_NOW: (ReplyKind.REPLY, SequenceStopReason.PAUSED, "medium", False),
    ReplyIntent.NOT_INTERESTED: (ReplyKind.REPLY, SequenceStopReason.REPLIED, "medium", False),
    ReplyIntent.OUT_OF_OFFICE: (ReplyKind.REPLY, SequenceStopReason.PAUSED, "low", False),
    ReplyIntent.WRONG_PERSON: (ReplyKind.REPLY, SequenceStopReason.REPLIED, "medium", True),
    ReplyIntent.UNSUBSCRIBE: (ReplyKind.SUSPECTED_OPTOUT, SequenceStopReason.PAUSED, "medium", True),
    ReplyIntent.UNCLEAR: (ReplyKind.REPLY, SequenceStopReason.REPLIED, "high", True),
}
# An empty body is never sent to the toolbox: no class, the unclassified behaviour.
UNCLASSIFIED = ("", ReplyKind.REPLY, SequenceStopReason.REPLIED, "high", False)


@pytest.fixture(autouse=True)
def profile(channel) -> ReplyClassProfile:
    return ReplyClassProfile.objects.create(channel=channel, prompt_text="Sort the reply.", model="fake-model")


def today(channel) -> date:
    return clock_service.now_for(channel).date()


def text_of(entry: dict, day: date) -> str:
    body = entry["body"]
    for days in (10, 14, 21, 30):
        body = body.replace(f"{{in{days}}}", (day + timedelta(days=days)).isoformat())
    return body


def reply_mail(body: str, subject: str) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Anna <{EMAIL}>", "outreach@mail.example.test", subject
    msg["Date"] = "Mon, 14 Sep 2026 10:15:00 +0200"
    msg["Message-ID"] = f"<golden-{abs(hash((body, subject)))}@example-shop-1.test>"
    msg["In-Reply-To"] = msg["References"] = OUR_ID
    msg.set_content(body)
    return msg.as_bytes()


def test_the_golden_set_covers_every_class_in_both_languages():
    per_class = Counter(entry["intent"] for entry in GOLDEN)
    assert set(per_class) == set(ReplyIntent.values) and min(per_class.values()) >= 5
    for intent in ReplyIntent.values:
        assert {entry["lang"] for entry in GOLDEN if entry["intent"] == intent} == {"pl", "en"}, intent


@pytest.mark.parametrize("entry", GOLDEN, ids=IDS)
def test_fake_toolbox_classifies_the_golden_reply(channel, entry):
    day = today(channel)
    own_text = mail_parser.unquoted(text_of(entry, day))
    with class_toolbox() as toolbox:
        result = reply_class_service.classify(channel, subject=entry["subject"], body=own_text, today=day)
    if entry.get("classified") is False:
        assert result is None and toolbox.count == 0
        return
    assert result is not None and result.intent == entry["intent"], (result, entry["body"])
    expected_date = day + timedelta(days=entry["date_offset"]) if "date_offset" in entry else None
    assert result.return_date == expected_date


@pytest.mark.parametrize("entry", GOLDEN, ids=IDS)
def test_golden_reply_end_to_end_with_the_default_plans(channel, entry, django_capture_on_commit_callbacks):
    message = make_sent(channel, EMAIL)
    classified = entry.get("classified") is not False
    intent, kind, stop_reason, severity, review = (
        (entry["intent"], *EXPECTED[entry["intent"]]) if classified else UNCLASSIFIED
    )

    with class_toolbox(), mock.patch(NOTIFY) as notify, django_capture_on_commit_callbacks(execute=True):
        reply = ingest(channel, reply_mail(text_of(entry, today(channel)), entry["subject"]))

    reply.refresh_from_db()
    assert (reply.intent, reply.kind) == (intent, kind), entry["body"]
    assert state_of(message).stop_reason == stop_reason
    assert notify.call_count == 1 and notify.call_args.kwargs["severity"] == severity
    assert reply.needs_review is review
    assert Thread.objects.get(pk=message.thread_id).status == ThreadStatus.REPLIED
    assert not Suppression.objects.exists()  # a reply never suppresses on its own
    assert (state_of(message).resume_at is not None) == ("date_offset" in entry)


def test_one_reply_one_toolbox_call(channel):
    make_sent(channel, EMAIL)
    with class_toolbox() as toolbox, mock.patch(NOTIFY):
        ingest(channel, reply_mail("Chętnie, proszę o cennik", "Re: Audit"))
    assert toolbox.count == 1 and Reply.objects.count() == 1
