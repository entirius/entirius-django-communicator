# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Outbound Q15 (sending health, auto-pause, manual resume) and Q30 (complaints and soft bounces into the limits)."""

from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from django_communicator.enums import ChannelMode, MessageStatus, ReplyKind, SuppressionKind
from django_communicator.models import HealthPolicy, Message, Reply, RoutePauseLog, SenderRoute, Suppression, Thread
from django_communicator.services import channel_service, health_service, inbound_service, message_service, send_service
from django_communicator.services.communicate_service import RecipientData, communicate
from django_communicator.signals import message_bounced
from tests.conftest import CHANNEL_IDX, MONDAY_10, api_url
from tests.test_inbound import OUR_ID, eml, go_live, make_sent

NOTIFY = "django_notifications.services.notify_service.notify"
NOW = MONDAY_10


@pytest.fixture
def live(policy):
    return channel_service.set_mode(policy.channel, mode=ChannelMode.LIVE, live_enabled=True)


@pytest.fixture
def routes(channel):
    from django_communicator.services import sender_route_service

    return (
        sender_route_service.create_route(channel, key="ecomhouse", from_email="a@ecom.house"),
        sender_route_service.create_route(channel, key="entirius", from_email="b@entirius.com"),
    )


def history(channel, route: str, n: int, *, ago: timedelta = timedelta(hours=1), prefix: str = "h") -> list[Message]:
    """`n` mails sent `ago` before NOW on `route`."""
    made = []
    for i in range(n):
        thread = Thread.objects.create(
            channel=channel,
            subject_ref=f"{prefix}:{route}:{i}",
            recipient_email=f"{prefix}{i}@{route}.test",
            sender_route=route,
        )
        made.append(
            Message.objects.create(
                thread=thread, status=MessageStatus.SENT, sent_at=NOW - ago, message_id=f"<{prefix}{route}{i}@x>"
            )
        )
    return made


def bounce(message: Message, kind: str = ReplyKind.BOUNCE_HARD, ago: timedelta = timedelta(minutes=30)) -> None:
    if kind == ReplyKind.BOUNCE_SOFT:
        Message.objects.filter(pk=message.pk).update(bounce_retry_at=NOW)
    Reply.objects.create(
        channel=message.thread.channel,
        thread=message.thread,
        message=message,
        from_email="mailer-daemon@x.test",
        kind=kind,
        matched_by="dsn",
        inbound_message_id=f"<b{message.pk}{kind}@x>",
        received_at=NOW - ago,
    )


def metrics_of(channel, window: str = "7d", route: str | None = None) -> dict:
    body = health_service.report(channel, NOW)
    if route is None:
        return body["channel"]["windows"][window]
    return next(r for r in body["routes"] if r["route_key"] == route)["windows"][window]


# --- Q15: the numbers ---


def test_Q15_counts_and_rates_per_route_and_channel(channel, routes):
    eco = history(channel, "ecomhouse", 10)
    ent = history(channel, "entirius", 5, prefix="g")
    bounce(eco[0])
    bounce(eco[1], ReplyKind.BOUNCE_SOFT)
    Reply.objects.create(
        channel=channel,
        thread=ent[0].thread,
        message=ent[0],
        from_email="x@y.test",
        kind=ReplyKind.REPLY,
        matched_by="header",
        inbound_message_id="<r1@x>",
        received_at=NOW - timedelta(minutes=5),
    )
    Reply.objects.create(
        channel=channel,
        thread=ent[1].thread,
        message=ent[1],
        from_email="x@y.test",
        kind=ReplyKind.COMPLAINT,
        matched_by="feedback",
        inbound_message_id="<c1@x>",
        received_at=NOW - timedelta(minutes=5),
    )
    Suppression.objects.create(
        channel=channel, kind=SuppressionKind.EMAIL, value=ent[2].thread.recipient_email, reason="unsubscribe link"
    )
    Suppression.objects.create(  # a manual suppression is not an opt-out
        channel=channel, kind=SuppressionKind.EMAIL, value=ent[3].thread.recipient_email, reason="manual"
    )

    eco_m, ent_m, all_m = (
        metrics_of(channel, route="ecomhouse"),
        metrics_of(channel, route="entirius"),
        metrics_of(channel),
    )

    assert (eco_m["sent"], eco_m["delivered"], eco_m["hard_bounces"], eco_m["soft_bounces"]) == (10, 9, 1, 1)
    assert (eco_m["hard_bounce_rate_pct"], eco_m["soft_bounce_rate_pct"]) == (10.0, 10.0)
    assert (ent_m["replies"], ent_m["complaints"], ent_m["optouts"], ent_m["optout_rate_pct"]) == (1, 1, 1, 20.0)
    assert (all_m["sent"], all_m["hard_bounces"], all_m["replies"], all_m["reply_rate_pct"]) == (15, 1, 1, 6.67)


def test_Q15_windows_24h_and_7d_and_empty_rates_are_null(channel, routes):
    history(channel, "ecomhouse", 4, ago=timedelta(hours=2))
    old = history(channel, "ecomhouse", 6, ago=timedelta(days=3), prefix="o")
    bounce(old[0], ago=timedelta(days=3))

    day, week = metrics_of(channel, "24h", "ecomhouse"), metrics_of(channel, "7d", "ecomhouse")
    nothing = metrics_of(channel, "24h", "entirius")

    assert (day["sent"], day["hard_bounces"]) == (4, 0)
    assert (week["sent"], week["hard_bounces"]) == (10, 1)
    assert nothing["sent"] == 0 and nothing["hard_bounce_rate_pct"] is None


def test_Q15_mail_without_a_key_counts_for_the_default_route(channel, routes):
    from django_communicator.services import sender_route_service

    history(channel, "", 3)
    sender_route_service.set_default(channel, "entirius")

    assert metrics_of(channel, route="entirius")["sent"] == 3


def test_Q15_dry_run_and_failed_mail_are_not_sent(channel, routes):
    made = history(channel, "ecomhouse", 2)
    Message.objects.filter(pk=made[0].pk).update(status=MessageStatus.WOULD_SEND)
    Message.objects.filter(pk=made[1].pk).update(status=MessageStatus.FAILED, failure_code="smtp")

    assert metrics_of(channel, route="ecomhouse")["sent"] == 0


def test_Q15_health_api_shape_and_staff_only(channel, routes, admin_api, customer_api):
    history(channel, "ecomhouse", 2, ago=timedelta(minutes=1))

    body = admin_api.get(api_url("health/")).json()

    assert set(body) == {"channel", "routes", "policy"}
    assert [r["route_key"] for r in body["routes"]] == ["ecomhouse", "entirius"]
    assert set(body["routes"][0]["windows"]) == {"24h", "7d"}
    assert body["policy"]["enabled"] is False
    assert customer_api.get(api_url("health/")).status_code == 403
    assert admin_api.get("/api/communicator/v2/admin/nope/health/").status_code == 404


def test_Q15_policy_api_defaults_off_validates_and_saves(channel, admin_api):
    default = admin_api.get(api_url("health/policy/")).json()
    assert default == {
        "enabled": False,
        "window_hours": 168,
        "min_sample": 50,
        "max_bounce_pct": 2.0,
        "max_optout_pct": 3.0,
        "max_complaint_pct": 0.1,
    }
    assert admin_api.put(api_url("health/policy/"), {"min_sample": 0}, format="json").status_code == 400
    assert admin_api.put(api_url("health/policy/"), {"bogus": 1}, format="json").status_code == 400

    saved = admin_api.put(
        api_url("health/policy/"), {"enabled": True, "min_sample": 20, "max_bounce_pct": 5}, format="json"
    )

    assert saved.status_code == 200 and saved.json()["enabled"] is True and saved.json()["max_bounce_pct"] == 5.0
    assert HealthPolicy.objects.get(channel=channel).min_sample == 20


# --- Q15: the automatic pause ---


def _policy(channel, **fields) -> HealthPolicy:
    return health_service.save_policy(channel, enabled=True, min_sample=10, **fields)


def test_Q15_nothing_is_paused_while_the_policy_is_off_or_missing(live, routes, channel):
    eco = history(channel, "ecomhouse", 10)
    for message in eco[:5]:
        bounce(message)

    assert health_service.evaluate(channel, NOW) == []
    health_service.save_policy(channel, enabled=False, min_sample=10)
    assert health_service.evaluate(channel, NOW) == []
    assert not SenderRoute.objects.filter(paused_at__isnull=False).exists()


def test_Q15_below_the_minimum_sample_nothing_is_paused(live, routes, channel):
    eco = history(channel, "ecomhouse", 5)
    bounce(eco[0])
    _policy(channel)

    assert health_service.evaluate(channel, NOW) == []


def test_Q15_pause_details(live, routes, channel):
    eco = history(channel, "ecomhouse", 100)
    history(channel, "entirius", 100, prefix="g")
    bounce(eco[0])
    bounce(eco[1])
    bounce(eco[2])
    _policy(channel)

    with mock.patch(NOTIFY) as notify:
        paused = health_service.evaluate(channel, NOW)
        again = health_service.evaluate(channel, NOW)

    eco_route, ent_route = SenderRoute.objects.get(key="ecomhouse"), SenderRoute.objects.get(key="entirius")
    assert (paused, again) == (["ecomhouse"], [])
    assert eco_route.paused_at is not None and "hard bounces 3.0%" in eco_route.pause_reason
    assert ent_route.paused_at is None
    assert notify.call_count == 1 and notify.call_args.kwargs["severity"] == "high"
    assert "ecomhouse" in notify.call_args.kwargs["title"]
    log = RoutePauseLog.objects.get()
    assert (log.route_key, log.action, log.trigger, log.user) == ("ecomhouse", "pause", "bounce", None)
    assert log.metrics["hard_bounces"] == 3 and log.metrics["sent"] == 100


def test_Q15_exactly_at_the_threshold_does_not_pause(live, routes, channel):
    eco = history(channel, "ecomhouse", 50)
    bounce(eco[0])  # 2.0%
    _policy(channel)

    assert health_service.evaluate(channel, NOW) == []


def test_Q15_optout_and_complaint_thresholds(live, routes, channel):
    eco = history(channel, "ecomhouse", 100)
    ent = history(channel, "entirius", 100, prefix="g")
    for message in eco[:4]:
        Suppression.objects.create(
            channel=channel, kind=SuppressionKind.EMAIL, value=message.thread.recipient_email, reason="optout confirmed"
        )
    Reply.objects.create(
        channel=channel,
        thread=ent[0].thread,
        message=ent[0],
        from_email="x@y.test",
        kind=ReplyKind.COMPLAINT,
        matched_by="feedback",
        inbound_message_id="<c1@x>",
        received_at=NOW - timedelta(minutes=5),
    )
    _policy(channel)

    with mock.patch(NOTIFY):
        assert health_service.evaluate(channel, NOW) == ["ecomhouse", "entirius"]

    triggers = dict(RoutePauseLog.objects.values_list("route_key", "trigger"))
    assert triggers == {"ecomhouse": "optout", "entirius": "complaint"}


def test_Q15_sandbox_channel_is_never_judged(policy, routes, channel):
    eco = history(channel, "ecomhouse", 20)
    bounce(eco[0])
    _policy(channel)

    assert health_service.evaluate(channel, NOW) == []


def _queue(route: str, ref: str) -> Message:
    return communicate(
        channel_idx=CHANNEL_IDX,
        template_key="followup",
        recipient=RecipientData(email=f"{ref}@fresh.test", first_name="Jan", language="pl"),
        context={},
        subject_ref=f"send:{ref}",
        requires_review=False,
        sender_route=route,
    )


def _live_run() -> dict:
    with override_settings(ENVIRONMENT="production"), mock.patch("django.utils.timezone.now", return_value=MONDAY_10):
        return dict(send_service.run_send_due())


def test_Q15_the_send_run_pauses_a_route_and_its_mail_waits_while_the_other_route_sends(
    live, static_template, routes, channel
):
    eco = history(channel, "ecomhouse", 20)
    bounce(eco[0])
    bounce(eco[1])
    _policy(channel)
    waiting, leaving = _queue("ecomhouse", "a"), _queue("entirius", "b")

    with mock.patch(NOTIFY):
        counts = _live_run()

    waiting.refresh_from_db(), leaving.refresh_from_db()
    assert (counts["sent"], counts["deferred"]) == (1, 1)
    assert leaving.status == MessageStatus.SENT and waiting.status == MessageStatus.APPROVED
    assert [m.to for m in mail.outbox] == [["b@fresh.test"]]


def test_Q15_the_channel_default_route_pause_holds_mail_without_a_key(live, static_template, routes, channel):
    from django_communicator.services import sender_route_service

    sender_route_service.set_default(channel, "ecomhouse")
    SenderRoute.objects.filter(key="ecomhouse").update(paused_at=NOW, pause_reason="x")
    waiting = _queue("", "a")

    assert _live_run()["sent"] == 0
    waiting.refresh_from_db()
    assert waiting.status == MessageStatus.APPROVED and not mail.outbox


def test_Q15_resume_needs_a_reason_logs_and_the_mail_goes_out(live, static_template, routes, channel, admin_api):
    SenderRoute.objects.filter(key="ecomhouse").update(paused_at=NOW, pause_reason="auto")
    waiting = _queue("ecomhouse", "a")
    assert _live_run()["sent"] == 0

    url = api_url("routes/ecomhouse/resume/")
    assert admin_api.post(url, {}, format="json").status_code == 400
    assert admin_api.post(url, {"reason": "x"}, format="json").status_code == 400
    done = admin_api.post(url, {"reason": "list cleaned, checked bounces"}, format="json")

    assert done.status_code == 200 and done.json()["paused"] is False and done.json()["pause_reason"] == ""
    assert admin_api.post(url, {"reason": "again please"}, format="json").status_code == 409
    log = RoutePauseLog.objects.get()
    assert (log.action, log.trigger, log.reason, log.user.username) == (
        "resume",
        "manual",
        "list cleaned, checked bounces",
        "reviewer",
    )
    assert _live_run()["sent"] == 1
    waiting.refresh_from_db()
    assert waiting.status == MessageStatus.SENT


def test_Q15_after_a_resume_old_bounces_do_not_pause_the_route_again(live, routes, channel, admin_api):
    eco = history(channel, "ecomhouse", 20)
    bounce(eco[0])
    bounce(eco[1])
    _policy(channel)
    with mock.patch(NOTIFY):
        health_service.evaluate(channel, NOW)
    route = SenderRoute.objects.get(key="ecomhouse")
    health_service.resume(route, NOW + timedelta(minutes=1), reason="checked", user=admin_api.user)

    assert health_service.evaluate(channel, NOW + timedelta(minutes=2)) == []
    assert SenderRoute.objects.get(key="ecomhouse").paused_at is None


def test_Q15_manual_pause_and_conflicts(live, routes, channel, admin_api):
    url = api_url("routes/entirius/pause/")
    assert admin_api.post(url, {}, format="json").status_code == 400

    first = admin_api.post(url, {"reason": "domain warm-up check"}, format="json")

    assert first.status_code == 200 and first.json()["paused"] is True
    assert admin_api.post(url, {"reason": "twice, why not"}, format="json").status_code == 409
    assert admin_api.post(api_url("routes/nope/pause/"), {"reason": "no such"}, format="json").status_code == 404
    entry = admin_api.get(api_url("health/log/")).json()["results"][0]
    assert (entry["action"], entry["trigger"], entry["user"]) == ("pause", "manual", "reviewer")
    assert admin_api.get(api_url("routes/entirius/")).json()["paused"] is True


def test_Q15_pause_endpoints_are_staff_only(live, routes, customer_api):
    assert (
        customer_api.post(api_url("routes/entirius/pause/"), {"reason": "no rights"}, format="json").status_code == 403
    )
    assert (
        customer_api.post(api_url("routes/entirius/resume/"), {"reason": "no rights"}, format="json").status_code == 403
    )


# --- Q30: complaints and bounces feed suppressions and the owner signal ---


@pytest.fixture
def bounced():
    calls = []

    def receiver(**kwargs):
        calls.append(kwargs)

    message_bounced.connect(receiver)
    yield calls
    message_bounced.disconnect(receiver)


def arf(feedback_type: str = "abuse", message_id: str = OUR_ID) -> bytes:
    return eml("arf_abuse.eml", message_id).replace(b"{feedback_type}", feedback_type.encode())


def test_Q30_complaints_are_ignored_while_the_feedback_loop_is_off(channel):
    go_live(channel)
    make_sent(channel, "owner@example-shop-6.test")

    assert inbound_service.ingest(channel, arf()) is None
    assert not Suppression.objects.exists() and not Reply.objects.exists()


@override_settings()
def test_Q30_live_complaint_suppresses_stops_the_sequence_notifies_and_signals(
    channel, bounced, django_capture_on_commit_callbacks, monkeypatch
):
    from django_communicator import settings as communicator_settings
    from tests.test_inbound import state_of

    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_COMPLAINT_FEEDBACK", True)
    go_live(channel)
    message = make_sent(channel, "owner@example-shop-6.test")

    with mock.patch(NOTIFY) as notify, django_capture_on_commit_callbacks(execute=True):
        reply = inbound_service.ingest(channel, arf())

    message.refresh_from_db()
    assert (reply.kind, reply.matched_by, reply.message_id) == (ReplyKind.COMPLAINT, "feedback", message.pk)
    assert message.status == MessageStatus.SENT
    row = Suppression.objects.get()
    assert (row.value, row.reason) == ("owner@example-shop-6.test", "complaint")
    assert state_of(message).stop_reason == "complaint"
    assert notify.call_args.kwargs["severity"] == "high" and notify.call_args.kwargs["title"] == "Spam complaint"
    assert [(c["hard"], c["reason"], c["message"].pk) for c in bounced] == [(True, "complaint", message.pk)]
    assert inbound_service.ingest(channel, arf()).pk == reply.pk  # idempotent


def test_Q30_complaint_in_sandbox_is_stored_but_changes_nothing(channel, bounced, monkeypatch):
    from django_communicator import settings as communicator_settings

    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_COMPLAINT_FEEDBACK", True)
    make_sent(channel, "owner@example-shop-6.test")

    reply = inbound_service.ingest(channel, arf())

    assert reply.kind == ReplyKind.COMPLAINT and not Suppression.objects.exists() and not bounced


def test_Q30_not_spam_reports_and_forged_recipients_are_ignored(channel, monkeypatch):
    from django_communicator import settings as communicator_settings

    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_COMPLAINT_FEEDBACK", True)
    go_live(channel)
    make_sent(channel, "owner@example-shop-6.test")

    assert inbound_service.ingest(channel, arf("not-spam")) is None
    forged = arf().replace(
        b"Original-Rcpt-To: <owner@example-shop-6.test>", b"Original-Rcpt-To: <victim@example-shop-9.test>"
    )
    assert inbound_service.ingest(channel, forged) is None
    assert not Suppression.objects.exists()


def test_Q30_hard_bounce_signal_and_suppression_on_live(channel, bounced, django_capture_on_commit_callbacks):
    go_live(channel)
    message = make_sent(channel, "owner@example-shop-6.test")

    with django_capture_on_commit_callbacks(execute=True):
        inbound_service.ingest(channel, eml("dsn_hard.eml"))

    assert Suppression.objects.get().reason == "dsn 5.1.1"
    assert [(c["hard"], c["message"].pk) for c in bounced] == [(True, message.pk)]


def _resend(message: Message, new_id: str) -> None:
    message_service.transition(message, MessageStatus.SENDING)
    message_service.transition(
        message, MessageStatus.SENT, fields={"message_id": new_id, "send_attempts": message.send_attempts + 1}
    )


def test_Q30_soft_bounces_count_towards_the_attempt_limit(channel, monkeypatch):
    from django_communicator import settings as communicator_settings

    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_SOFT_BOUNCE_MAX_ATTEMPTS", 3)
    message = make_sent(channel, "owner@example-shop-6.test")
    Message.objects.filter(pk=message.pk).update(send_attempts=1)
    inbound_service.ingest(channel, eml("dsn_soft.eml"))  # attempt 1 bounced: retry
    message.refresh_from_db()
    assert message.status == MessageStatus.SCHEDULED
    Message.objects.filter(pk=message.pk).update(send_attempts=1)
    message.refresh_from_db()
    _resend(message, "<r2@mail.example.test>")  # attempt 2

    inbound_service.ingest(
        channel, eml("dsn_soft.eml", "<r2@mail.example.test>").replace(b"<dsn-soft-1@", b"<dsn-soft-2@")
    )
    message.refresh_from_db()
    assert message.status == MessageStatus.SCHEDULED  # a third attempt is still allowed
    _resend(message, "<r3@mail.example.test>")  # attempt 3

    inbound_service.ingest(
        channel, eml("dsn_soft.eml", "<r3@mail.example.test>").replace(b"<dsn-soft-1@", b"<dsn-soft-3@")
    )
    message.refresh_from_db()
    assert (message.status, message.failure_code, message.send_attempts) == (MessageStatus.FAILED, "bounce", 3)


def test_Q30_default_limit_is_one_retry(channel):
    message = make_sent(channel, "owner@example-shop-6.test")
    Message.objects.filter(pk=message.pk).update(send_attempts=2)

    inbound_service.ingest(channel, eml("dsn_soft.eml"))

    message.refresh_from_db()
    assert (message.status, message.failure_code) == (MessageStatus.FAILED, "bounce")
