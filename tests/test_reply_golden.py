# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""T4 / S12: the golden set of sample Polish and English replies (`tests/fixtures/reply_golden.json`, a copy of the
leads one — keep them equal) against the FAKE toolbox, asserting the whole communicator mapping per class end to end.

The same set runs against a real toolbox with `T4_REAL_TOOLBOX=1` (see `test_real_toolbox_matches_the_golden_set`): it
is skipped here, so how well a real model classifies is not verified by this suite."""

import json
import os
from collections import Counter
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import ReplyIntent, ReplyKind, SequenceStopReason, ThreadStatus
from django_communicator.models import Reply, Suppression, Thread
from django_communicator.services import mail_parser, reply_intent_service
from django_communicator.services.inbound_service import ingest
from tests.fake_toolbox import intent_toolbox
from tests.test_inbound import NOTIFY, make_sent, state_of
from tests.test_reply_intent import reply_mail

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "reply_golden.json").read_text())
EMAIL = "owner@example-shop-1.test"
IDS = [f"{g['intent']}-{g['lang']}-{n}" for n, g in enumerate(GOLDEN)]

# What each class must do with the default mapping: (reply kind, sequence stop reason, alert severity, needs_review)
EXPECTED = {
    ReplyIntent.INTERESTED: (ReplyKind.REPLY, SequenceStopReason.REPLIED, "high", False),
    ReplyIntent.NOT_NOW: (ReplyKind.REPLY, SequenceStopReason.PAUSED, "medium", False),
    ReplyIntent.NOT_INTERESTED: (ReplyKind.REPLY, SequenceStopReason.REPLIED, "medium", False),
    ReplyIntent.OUT_OF_OFFICE: (ReplyKind.REPLY, SequenceStopReason.PAUSED, "low", False),
    ReplyIntent.WRONG_PERSON: (ReplyKind.REPLY, SequenceStopReason.REPLIED, "medium", True),
    ReplyIntent.UNSUBSCRIBE: (ReplyKind.SUSPECTED_OPTOUT, SequenceStopReason.PAUSED, "medium", False),
    ReplyIntent.UNCLEAR: (ReplyKind.REPLY, SequenceStopReason.REPLIED, "high", True),
}
# An empty body is never sent to the toolbox: the pre-T4 treatment.
UNCLASSIFIED = ("", ReplyKind.REPLY, SequenceStopReason.REPLIED, "high", False)


def text_of(entry: dict) -> str:
    body = entry["body"]
    for days in (10, 14, 21, 30):
        body = body.replace(f"{{in{days}}}", (timezone.now() + timedelta(days=days)).date().isoformat())
    return body


@pytest.fixture(autouse=True)
def intent_model(monkeypatch):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_REPLY_INTENT_MODEL", "fake-model")


def test_the_golden_set_covers_every_class_in_both_languages():
    per_class = Counter(g["intent"] for g in GOLDEN)
    assert set(per_class) == set(ReplyIntent.values) and min(per_class.values()) >= 5
    for intent in ReplyIntent.values:
        if intent != ReplyIntent.UNCLEAR:
            assert {g["lang"] for g in GOLDEN if g["intent"] == intent} == {"pl", "en"}, intent


@pytest.mark.parametrize("entry", GOLDEN, ids=IDS)
def test_fake_toolbox_classifies_the_golden_reply(channel, entry):
    own_text = mail_parser.unquoted(text_of(entry))
    with intent_toolbox() as toolbox:
        result = reply_intent_service.classify(
            channel, subject=entry["subject"], body=own_text, today=timezone.now().date()
        )
    if entry.get("classified") is False:
        assert result is None and toolbox.count == 0
        return
    assert result is not None and result.intent == entry["intent"], (result, entry["body"])
    expected_date = (timezone.now() + timedelta(days=entry["date_offset"])).date() if "date_offset" in entry else None
    assert result.return_date == expected_date


@pytest.mark.parametrize("entry", GOLDEN, ids=IDS)
def test_golden_reply_end_to_end_with_the_default_mapping(channel, entry):
    message = make_sent(channel, EMAIL)
    classified = entry.get("classified") is not False
    intent, kind, stop_reason, severity, review = (
        (entry["intent"], *EXPECTED[entry["intent"]]) if classified else UNCLASSIFIED
    )

    with intent_toolbox(), mock.patch(NOTIFY) as notify:
        reply = ingest(channel, reply_mail(text_of(entry), subject=entry["subject"]))

    reply.refresh_from_db()
    assert (reply.intent, reply.kind) == (intent, kind), entry["body"]
    assert state_of(message).stop_reason == stop_reason
    assert notify.call_count == 1 and notify.call_args.kwargs["severity"] == severity
    assert reply.needs_review is review
    assert Thread.objects.get(pk=message.thread_id).status == ThreadStatus.REPLIED
    assert not Suppression.objects.exists()  # a golden reply never suppresses on its own
    if intent == ReplyIntent.OUT_OF_OFFICE:
        dated = "date_offset" in entry
        assert (reply.resume_at is not None, reply.remind_at is None) == (dated, dated)
    elif intent == ReplyIntent.NOT_NOW:
        assert reply.remind_at is not None and reply.resume_at is None
    else:
        assert reply.remind_at is None and reply.resume_at is None


def test_one_reply_one_toolbox_call_and_nothing_else_is_sent(channel):
    make_sent(channel, EMAIL)
    with intent_toolbox() as toolbox, mock.patch(NOTIFY):
        ingest(channel, reply_mail("Chętnie, proszę o cennik"))
    assert toolbox.count == 1 and Reply.objects.count() == 1


@pytest.mark.skipif(not os.environ.get("T4_REAL_TOOLBOX"), reason="needs a real AI toolbox (T4_REAL_TOOLBOX=1)")
def test_real_toolbox_matches_the_golden_set(channel, settings, monkeypatch):
    """Opt-in: the same golden set through the real `ToolboxClient`. Set `T4_TOOLBOX_URL`, `T4_TOOLBOX_KEY`,
    `T4_TOOLBOX_CHANNEL` and `T4_TOOLBOX_MODEL`. Prints the misses; fails under 90 % agreement or on any
    `unsubscribe` golden reply classified as something that is not unsubscribe or unclear."""
    settings.AI_TOOLBOX_ENABLED = True
    settings.AI_TOOLBOX_BASE_URL = os.environ["T4_TOOLBOX_URL"]
    settings.AI_TOOLBOX_API_KEY = os.environ["T4_TOOLBOX_KEY"]
    settings.AI_TOOLBOX_CHANNEL = os.environ["T4_TOOLBOX_CHANNEL"]
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_REPLY_INTENT_MODEL", os.environ["T4_TOOLBOX_MODEL"])
    misses = []
    entries = [g for g in GOLDEN if g.get("classified") is not False]
    for entry in entries:
        result = reply_intent_service.classify(
            channel,
            subject=entry["subject"],
            body=mail_parser.unquoted(text_of(entry)),
            today=timezone.now().date(),
        )
        got = result.intent if result else None
        if got != entry["intent"]:
            misses.append((entry["intent"], got, entry["body"][:60]))
    print(f"\nreal toolbox: {len(entries) - len(misses)}/{len(entries)} agree")
    for miss in misses:
        print("  miss:", miss)
    dangerous = [m for m in misses if m[0] == ReplyIntent.UNSUBSCRIBE and m[1] not in (ReplyIntent.UNCLEAR, None)]
    assert not dangerous
    assert len(misses) <= len(entries) * 0.1
