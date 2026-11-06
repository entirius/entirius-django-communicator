# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""T4 / S11: the next step per intent class and channel (defaults, admin API, application), reminders and the resume
of a sequence paused until a return date. The toolbox is the FAKE one (`tests/fake_toolbox.py`)."""

from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from django_communicator import settings as communicator_settings
from django_communicator.enums import ReplyIntent, ReplyKind, SequenceStopReason, ThreadStatus
from django_communicator.models import Channel, Reply, ReplyIntentAction, SequenceStep, Suppression, Thread
from django_communicator.services import clock_service, reply_action_service, reply_followup_service
from django_communicator.signals import optout_confirmed, reply_followup_due
from tests.conftest import api_url
from tests.fake_toolbox import intent_toolbox
from tests.test_inbound import NOTIFY, make_sent, state_of
from tests.test_reply_intent import ingest_text, reply_mail

EMAIL = "owner@example-shop-1.test"
WARSAW = ZoneInfo("Europe/Warsaw")


@pytest.fixture(autouse=True)
def intent_model(monkeypatch):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_REPLY_INTENT_MODEL", "fake-model")


def classify(channel, body: str, **kwargs) -> tuple[Reply, mock.MagicMock]:
    """One reply through the fake toolbox; returns it with the notify mock."""
    from django_communicator.services.inbound_service import ingest

    with intent_toolbox(), mock.patch(NOTIFY) as notify:
        return ingest(channel, reply_mail(body, **kwargs)), notify


def assert_plan(reply: Reply, sequence_reason: str, message) -> None:
    assert state_of(message).stop_reason == sequence_reason


# --- defaults and the admin API ---------------------------------------------------------------------------------------


def test_defaults_are_safe_and_complete(channel, admin_api):
    rows = admin_api.get(api_url("reply-actions/")).json()["results"]
    assert [row["intent"] for row in rows] == ReplyIntent.values
    assert all(row["is_default"] for row in rows)
    by_intent = {row["intent"]: row for row in rows}
    assert not any(row["auto_suppress"] for row in rows)  # nothing is ever suppressed by default
    assert {i: by_intent[i]["sequence"] for i in by_intent} == {
        "interested": "stop",
        "not_now": "pause",
        "not_interested": "stop",
        "out_of_office": "pause_until_return",
        "wrong_person": "stop",
        "unsubscribe": "stop",
        "unclear": "stop",
    }
    assert by_intent["not_now"]["remind_after_days"] == 30 and by_intent["unclear"]["needs_review"]
    assert by_intent["unclear"]["notify"] == "high" and by_intent["not_interested"]["notify"] == "medium"


def test_put_replaces_one_class_and_delete_resets_it(channel, admin_api):
    url = api_url("reply-actions/not_now/")
    body = {"sequence": "stop", "notify": "low", "remind_after_days": 90, "needs_review": True, "min_confidence": 0.8}
    saved = admin_api.put(url, body, format="json")
    assert saved.status_code == 200 and saved.json() | {"is_default": False} == {
        "intent": "not_now",
        **body,
        "auto_suppress": False,
        "is_default": False,
    }
    rows = {r["intent"]: r for r in admin_api.get(api_url("reply-actions/")).json()["results"]}
    assert rows["not_now"]["remind_after_days"] == 90 and rows["interested"]["is_default"]

    again = admin_api.put(url, {"notify": "none"}, format="json").json()  # a PUT replaces: omitted = model default
    assert (again["sequence"], again["notify"], again["remind_after_days"], again["needs_review"]) == (
        "stop",
        "none",
        None,
        False,
    )
    assert ReplyIntentAction.objects.count() == 1  # updated, not duplicated

    reset = admin_api.delete(url).json()
    assert reset["is_default"] and reset["sequence"] == "pause" and reset["remind_after_days"] == 30
    assert admin_api.delete(url).status_code == 200  # idempotent
    assert not ReplyIntentAction.objects.exists()


@pytest.mark.parametrize(
    ("intent", "body"),
    [
        ("interested", {"auto_suppress": True}),
        ("not_now", {"sequence": "pause_until_return"}),
        ("not_now", {"remind_after_days": 0}),
        ("not_now", {"remind_after_days": 366}),
        ("not_now", {"min_confidence": 1.5}),
        ("not_now", {"min_confidence": -0.1}),
        ("not_now", {"sequence": "send_more"}),
        ("not_now", {"notify": "loud"}),
        ("not_now", {"surprise": 1}),
    ],
)
def test_put_refuses_nonsense(channel, admin_api, intent, body):
    assert admin_api.put(api_url(f"reply-actions/{intent}/"), body, format="json").status_code == 400
    assert not ReplyIntentAction.objects.exists()


def test_put_accepts_the_allowed_special_cases(channel, admin_api):
    assert (
        admin_api.put(
            api_url("reply-actions/out_of_office/"), {"sequence": "pause_until_return"}, format="json"
        ).status_code
        == 200
    )
    assert (
        admin_api.put(api_url("reply-actions/unsubscribe/"), {"auto_suppress": True}, format="json").status_code == 200
    )


def test_unknown_intent_and_channel_are_404(channel, admin_api):
    assert admin_api.put(api_url("reply-actions/banana/"), {}, format="json").status_code == 404
    assert admin_api.delete(api_url("reply-actions/banana/")).status_code == 404
    assert admin_api.get("/api/communicator/v2/admin/nope/reply-actions/").status_code == 404


def test_mapping_is_per_channel(channel, admin_api):
    other = Channel.objects.create(idx="other-channel", label="Other")
    admin_api.put(api_url("reply-actions/not_now/"), {"notify": "none"}, format="json")
    assert reply_action_service.plan_for(channel, "not_now").notify == "none"
    assert reply_action_service.plan_for(other, "not_now").notify == "medium"


def test_only_staff_can_read_or_write(channel, customer_api):
    assert customer_api.get(api_url("reply-actions/")).status_code == 403
    assert customer_api.put(api_url("reply-actions/not_now/"), {}, format="json").status_code == 403
    assert APIClient().get(api_url("reply-actions/")).status_code == 401


# --- applying the plan to a reply -------------------------------------------------------------------------------------


def test_interested_stops_the_sequence_and_alerts_high(channel):
    message = make_sent(channel, EMAIL)
    reply, notify = classify(channel, "Chętnie, proszę o cennik")
    assert_plan(reply, SequenceStopReason.REPLIED, message)
    assert notify.call_args.kwargs["severity"] == "high" and "(interested)" in notify.call_args.kwargs["title"]
    reply.refresh_from_db()
    assert (reply.needs_review, reply.resume_at, reply.remind_at) == (False, None, None)
    assert reply.action_applied["sequence"] == "stop" and reply.action_applied["is_default"]
    assert Thread.objects.get(pk=message.thread_id).status == ThreadStatus.REPLIED


def test_not_now_pauses_and_sets_a_reminder(channel):
    message = make_sent(channel, EMAIL)
    before = clock_service.now_for(channel)
    reply, notify = classify(channel, "Nie teraz, wróćmy do tematu w przyszłym kwartale")
    assert reply.intent == ReplyIntent.NOT_NOW and reply.kind == ReplyKind.REPLY
    assert state_of(message).stop_reason == SequenceStopReason.PAUSED and state_of(message).next_due_at is None
    assert notify.call_args.kwargs["severity"] == "medium"
    reply.refresh_from_db()
    assert timedelta(days=29, hours=23) < reply.remind_at - before < timedelta(days=30, minutes=1)
    assert reply.resume_at is None
    assert Thread.objects.get(pk=message.thread_id).status == ThreadStatus.REPLIED


def test_out_of_office_with_a_date_pauses_until_the_day_after(channel):
    message = make_sent(channel, EMAIL)
    back = (timezone.now() + timedelta(days=10)).date()
    reply, notify = classify(channel, f"Jestem poza biurem do {back.isoformat()}.")
    reply.refresh_from_db()
    assert state_of(message).stop_reason == SequenceStopReason.PAUSED
    expected = datetime(back.year, back.month, back.day, tzinfo=WARSAW) + timedelta(days=1)
    assert reply.resume_at == expected and reply.resume_at.astimezone(WARSAW).hour == 0 and reply.remind_at is None
    assert notify.call_args.kwargs["severity"] == "low"


def test_out_of_office_without_a_date_pauses_and_reminds_a_person(channel):
    message = make_sent(channel, EMAIL)
    reply, _ = classify(channel, "Jestem poza biurem, odpowiem po powrocie.")
    reply.refresh_from_db()
    assert reply.intent == ReplyIntent.OUT_OF_OFFICE and reply.resume_at is None
    assert state_of(message).stop_reason == SequenceStopReason.PAUSED
    assert reply.remind_at is not None and reply.remind_at - clock_service.now_for(channel) < timedelta(days=7)


@pytest.mark.parametrize(
    ("body", "intent", "severity", "review"),
    [
        ("Nie jestem zainteresowany, dziękuję.", ReplyIntent.NOT_INTERESTED, "medium", False),
        ("To nie ta osoba, proszę skontaktować się z kolegą", ReplyIntent.WRONG_PERSON, "medium", True),
        ("ok", ReplyIntent.UNCLEAR, "high", True),
    ],
)
def test_stop_classes_with_their_alert_and_review_flag(channel, body, intent, severity, review):
    message = make_sent(channel, EMAIL)
    reply, notify = classify(channel, body)
    assert reply.intent == intent
    assert state_of(message).stop_reason == SequenceStopReason.REPLIED
    assert notify.call_args.kwargs["severity"] == severity
    reply.refresh_from_db()
    assert reply.needs_review is review


def test_unclassified_reply_gets_exactly_the_old_treatment(channel, settings):
    settings.AI_TOOLBOX_ENABLED = False
    message = make_sent(channel, EMAIL)
    with mock.patch(NOTIFY) as notify:
        from django_communicator.services.inbound_service import ingest

        reply = ingest(channel, reply_mail("Chętnie porozmawiamy"))
    assert reply.intent == "" and state_of(message).stop_reason == SequenceStopReason.REPLIED
    assert notify.call_args.kwargs == {
        "channel_idx": channel.idx,
        "recipient_role": "sales_admin",
        "severity": "high",
        "subject_ref": "leads.Company:1",
        "title": f"Reply from {EMAIL}",
    }
    reply.refresh_from_db()
    assert (reply.needs_review, reply.remind_at, reply.resume_at) == (False, None, None)


def test_a_stored_mapping_changes_what_happens(channel, admin_api):
    admin_api.put(
        api_url("reply-actions/interested/"),
        {"sequence": "pause", "notify": "none", "remind_after_days": 3, "needs_review": True},
        format="json",
    )
    message = make_sent(channel, EMAIL)
    reply, notify = classify(channel, "Chętnie, proszę o cennik")
    reply.refresh_from_db()
    assert state_of(message).stop_reason == SequenceStopReason.PAUSED
    assert notify.call_count == 0  # silent on request
    assert reply.needs_review and reply.remind_at is not None and not reply.action_applied["is_default"]


def test_a_class_threshold_of_its_own(channel, admin_api):
    admin_api.put(api_url("reply-actions/interested/"), {"min_confidence": 0.95}, format="json")
    make_sent(channel, EMAIL)
    reply, _ = classify(channel, "Chętnie")  # the fake is 0.9 sure
    assert reply.intent == ReplyIntent.UNCLEAR and reply.intent_confidence == 0.9


def test_replies_list_filters_by_needs_review(channel, admin_api):
    make_sent(channel, EMAIL)
    unclear, _ = classify(channel, "ok")
    make_sent(channel, "b@example-shop-2.test", message_id="<b@x.test>", ref="leads.Company:2")
    with mock.patch(NOTIFY), intent_toolbox():
        fine = ingest_text(channel, "Chętnie", sender="b@example-shop-2.test", in_reply_to="<b@x.test>")
    url = api_url("replies/")
    assert [r["id"] for r in admin_api.get(url, {"needs_review": "true"}).json()["results"]] == [unclear.pk]
    assert [r["id"] for r in admin_api.get(url, {"needs_review": "false"}).json()["results"]] == [fine.pk]
    assert len(admin_api.get(url).json()["results"]) == 2


# --- unsubscribe: the same confirm as a person's click ----------------------------------------------------------------


@pytest.fixture
def optouts():
    calls = []

    def receiver(**kwargs):
        calls.append(kwargs)

    optout_confirmed.connect(receiver)
    yield calls
    optout_confirmed.disconnect(receiver)


def unsubscribe_text(body: str = "Proszę o wypisanie z listy"):
    return body


def test_unsubscribe_default_waits_for_a_person(channel):
    message = make_sent(channel, EMAIL)
    reply, notify = classify(channel, "Please unsubscribe me")
    assert reply.kind == ReplyKind.SUSPECTED_OPTOUT and not Suppression.objects.exists()
    assert (
        state_of(message).stop_reason == SequenceStopReason.PAUSED and notify.call_args.kwargs["severity"] == "medium"
    )


def test_unsubscribe_auto_suppress_confirms_like_a_person(
    channel, admin_api, optouts, django_capture_on_commit_callbacks
):
    admin_api.put(api_url("reply-actions/unsubscribe/"), {"auto_suppress": True, "notify": "low"}, format="json")
    message = make_sent(channel, EMAIL)
    with django_capture_on_commit_callbacks(execute=True):
        reply, notify = classify(channel, "Please unsubscribe me")
    reply.refresh_from_db()
    suppression = Suppression.objects.get()
    assert (suppression.value, suppression.reason, suppression.created_by) == (EMAIL, "optout confirmed", None)
    assert state_of(message).stop_reason == SequenceStopReason.OPTOUT
    assert reply.optout_confirmed_at is not None and reply.optout_confirmed_by is None
    assert [call["email"] for call in optouts] == [EMAIL]
    assert notify.call_args.kwargs["severity"] == "low"


def test_auto_suppress_never_fires_for_a_phrase_only_suspicion(channel, admin_api, settings):
    admin_api.put(api_url("reply-actions/unsubscribe/"), {"auto_suppress": True}, format="json")
    settings.AI_TOOLBOX_ENABLED = False  # no class: the phrase list alone raised it
    make_sent(channel, EMAIL, language="EN")
    from django_communicator.services.inbound_service import ingest

    with mock.patch(NOTIFY):
        reply = ingest(channel, reply_mail("Please unsubscribe me"))
    assert reply.kind == ReplyKind.SUSPECTED_OPTOUT and not Suppression.objects.exists()


def test_auto_suppress_not_when_the_class_is_unsure(channel, admin_api):
    admin_api.put(api_url("reply-actions/unsubscribe/"), {"auto_suppress": True}, format="json")
    make_sent(channel, EMAIL, language="EN")
    from django_communicator.services.inbound_service import ingest

    with intent_toolbox(lambda *_: {"intent": "unsubscribe", "confidence": 0.5}), mock.patch(NOTIFY):
        reply = ingest(channel, reply_mail("hmm remove"))
    assert reply.intent == ReplyIntent.UNCLEAR and not Suppression.objects.exists()


def test_unsubscribe_notify_none_is_silent(channel, admin_api):
    admin_api.put(api_url("reply-actions/unsubscribe/"), {"notify": "none"}, format="json")
    make_sent(channel, EMAIL)
    reply, notify = classify(channel, "Please unsubscribe me")
    assert reply.kind == ReplyKind.SUSPECTED_OPTOUT and notify.call_count == 0


# --- reminders and resume ---------------------------------------------------------------------------------------------


@pytest.fixture
def followups():
    calls = []

    def receiver(**kwargs):
        calls.append(kwargs)

    reply_followup_due.connect(receiver)
    yield calls
    reply_followup_due.disconnect(receiver)


def move_clock(channel, **delta):
    clock_service.set_override(channel, timezone.now() + timedelta(**delta))


def test_reminder_comes_due_once(channel, followups, django_capture_on_commit_callbacks):
    make_sent(channel, EMAIL)
    reply, _ = classify(channel, "Nie teraz, może w przyszłym roku")
    with mock.patch(NOTIFY) as notify, django_capture_on_commit_callbacks(execute=True):
        assert reply_followup_service.run_due() == {"resumed": 0, "reminded": 0}
        move_clock(channel, days=31)
        assert reply_followup_service.run_due() == {"resumed": 0, "reminded": 1}
        assert reply_followup_service.run_due() == {"resumed": 0, "reminded": 0}
    assert notify.call_count == 1 and "Reminder" in notify.call_args.kwargs["title"]
    assert [call["reply"].pk for call in followups] == [reply.pk]
    reply.refresh_from_db()
    assert reply.reminded_at is not None


def test_out_of_office_sequence_resumes_after_the_return(channel):
    message = make_sent(channel, EMAIL)
    SequenceStep.objects.create(sequence=state_of(message).sequence, number=1, days_after_previous=3, template_key="f")
    back = (timezone.now() + timedelta(days=10)).date()
    classify(channel, f"Jestem poza biurem do {back.isoformat()}.")
    assert state_of(message).stop_reason == SequenceStopReason.PAUSED

    move_clock(channel, days=10)  # the day of the return: not yet
    assert reply_followup_service.run_due() == {"resumed": 0, "reminded": 0}
    assert state_of(message).stop_reason == SequenceStopReason.PAUSED

    move_clock(channel, days=12)
    assert reply_followup_service.run_due() == {"resumed": 1, "reminded": 0}
    state = state_of(message)
    assert state.stopped_at is None and state.stop_reason == "" and state.next_due_at is not None
    assert Thread.objects.get(pk=message.thread_id).status == ThreadStatus.OPEN
    assert reply_followup_service.run_due() == {"resumed": 0, "reminded": 0}


def test_resume_is_skipped_when_a_later_reply_arrived(channel):
    message = make_sent(channel, EMAIL)
    back = (timezone.now() + timedelta(days=5)).date()
    first, _ = classify(channel, f"Jestem poza biurem do {back.isoformat()}.")
    later = classify(channel, "Chętnie, proszę o cennik")[0]
    assert later.pk != first.pk
    move_clock(channel, days=8)
    assert reply_followup_service.run_due()["resumed"] == 0
    assert state_of(message).stop_reason != "" and Reply.objects.get(pk=first.pk).resumed_at is not None


def test_resume_is_skipped_when_the_address_was_suppressed_meanwhile(channel):
    from django_communicator.services import suppression_service

    message = make_sent(channel, EMAIL)
    back = (timezone.now() + timedelta(days=5)).date()
    classify(channel, f"Jestem poza biurem do {back.isoformat()}.")
    suppression_service.suppress_email(channel, EMAIL, reason="manual")
    move_clock(channel, days=8)
    assert reply_followup_service.run_due()["resumed"] == 0
    assert state_of(message).stop_reason == SequenceStopReason.SUPPRESSED


def test_resume_is_skipped_for_a_closed_thread_and_a_stopped_sequence(channel):
    closed = make_sent(channel, EMAIL)
    back = (timezone.now() + timedelta(days=5)).date()
    classify(channel, f"Jestem poza biurem do {back.isoformat()}.")
    Thread.objects.filter(pk=closed.thread_id).update(status=ThreadStatus.CLOSED)

    stopped = make_sent(channel, "b@example-shop-2.test", message_id="<b@x.test>", ref="leads.Company:2")
    with mock.patch(NOTIFY), intent_toolbox():
        ingest_text(
            channel,
            f"Jestem poza biurem do {back.isoformat()}.",
            sender="b@example-shop-2.test",
            in_reply_to="<b@x.test>",
        )
    from django_communicator.services import sequence_service

    sequence_service.stop_sequence(stopped.thread, SequenceStopReason.MANUAL)

    move_clock(channel, days=8)
    assert reply_followup_service.run_due()["resumed"] == 0
    assert Thread.objects.get(pk=closed.thread_id).status == ThreadStatus.CLOSED
    assert state_of(stopped).stop_reason == SequenceStopReason.MANUAL


def test_a_failing_row_does_not_stop_the_run(channel):
    for index in range(2):
        make_sent(
            channel, f"p{index}@example-shop-1.test", message_id=f"<m{index}@x.test>", ref=f"leads.Company:{index}"
        )
        with mock.patch(NOTIFY), intent_toolbox():
            ingest_text(
                channel, f"Nie teraz {index}", sender=f"p{index}@example-shop-1.test", in_reply_to=f"<m{index}@x.test>"
            )
    move_clock(channel, days=40)
    calls = {"n": 0}
    real = reply_followup_service._remind

    def flaky(reply):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(reply)

    with mock.patch.object(reply_followup_service, "_remind", flaky), mock.patch(NOTIFY):
        assert reply_followup_service.run_due()["reminded"] == 1


def test_dev_endpoint_and_task_run_the_followups(channel, admin_api, once_backend):
    from django_communicator.tasks import process_reply_followups

    assert process_reply_followups.name == "django_communicator.process_reply_followups"
    assert admin_api.post(api_url("test/reply-followups/")).json() == {"resumed": 0, "reminded": 0}
    assert process_reply_followups.run() == {"resumed": 0, "reminded": 0}
    key = process_reply_followups.get_key((), {})
    process_reply_followups.once_backend.raise_or_lock(key, timeout=60)
    try:
        assert admin_api.post(api_url("test/reply-followups/")).status_code == 409
    finally:
        process_reply_followups.once_backend.clear_lock(key)


def test_gdpr_erase_still_scrubs_a_classified_reply(channel):
    from django_communicator.gdpr import gdpr_erase

    make_sent(channel, EMAIL)
    reply, _ = classify(channel, "Chętnie, proszę o cennik")
    gdpr_erase(EMAIL)
    reply.refresh_from_db()
    assert "cennik" not in reply.body_text and EMAIL not in reply.from_email
