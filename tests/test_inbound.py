# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import imaplib
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from django_communicator.enums import MessageStatus, ReplyKind, ReplyMatch, SequenceStopReason, ThreadStatus
from django_communicator.models import MailboxConfig, Message, Reply, Sequence, Suppression, Thread, ThreadSequenceState
from django_communicator.services import message_service, optout_service, poll_service
from django_communicator.services.inbound_service import ingest
from django_communicator.signals import optout_confirmed, reply_received
from tests.conftest import api_url
from tests.factories import LanguageFactory

NOTIFY = "django_notifications.services.notify_service.notify"
MAIL = Path(__file__).parent / "fixtures" / "mail"
OUR_ID = "<communicator-1-abcd1234@mail.example.test>"
IMAP_ERROR = imaplib.IMAP4.error  # kept before the tests patch imaplib.IMAP4


def eml(name: str, message_id: str = OUR_ID) -> bytes:
    return (MAIL / name).read_bytes().replace(b"{message_id}", message_id.encode())


def make_sent(channel, email: str, *, language: str = "PL", message_id: str = OUR_ID, ref: str = "leads.Company:1"):
    """A sent outbound message on a fresh thread with a running sequence."""
    thread = Thread.objects.create(
        channel=channel, subject_ref=ref, recipient_email=email, recipient_language=LanguageFactory(iso2=language)
    )
    message = Message.objects.create(
        thread=thread, status=MessageStatus.SENT, message_id=message_id, sent_at=timezone.now(), subject="Audit"
    )
    sequence = Sequence.objects.create(channel=channel, key=f"seq-{thread.pk}")
    ThreadSequenceState.objects.create(thread=thread, sequence=sequence, next_due_at=timezone.now())
    return message


def state_of(message: Message) -> ThreadSequenceState:
    return ThreadSequenceState.objects.get(thread_id=message.thread_id)


@pytest.fixture
def received():
    calls = []

    def receiver(**kwargs):
        calls.append(kwargs)

    reply_received.connect(receiver)
    yield calls
    reply_received.disconnect(receiver)


def test_C20_reply_by_in_reply_to_marks_thread_stops_sequence_notifies(
    channel, received, django_capture_on_commit_callbacks
):
    message = make_sent(channel, "owner@example-shop-1.test")

    with mock.patch(NOTIFY) as notify, django_capture_on_commit_callbacks(execute=True):
        reply = ingest(channel, eml("reply_plain.eml"))

    message.refresh_from_db()
    assert (reply.kind, reply.matched_by, reply.message_id) == (ReplyKind.REPLY, ReplyMatch.HEADER, message.pk)
    assert "can we talk on Thursday" in reply.body_text and "Message-ID" in reply.raw_headers
    assert Thread.objects.get(pk=message.thread_id).status == ThreadStatus.REPLIED
    assert message.replied_at is not None and state_of(message).stop_reason == SequenceStopReason.REPLIED
    assert notify.call_count == 1 and notify.call_args.kwargs["severity"] == "high"
    assert notify.call_args.kwargs["subject_ref"] == "leads.Company:1"
    assert [call["reply"] for call in received] == [reply]


def test_C21_reply_without_headers_matches_newest_open_thread_by_sender(channel):
    older = make_sent(channel, "owner@example-shop-2.test", message_id="<a@mail.example.test>", ref="x:1")
    newer = make_sent(channel, "Owner@Example-Shop-2.test", message_id="<b@mail.example.test>", ref="x:2")
    closed = make_sent(channel, "owner@example-shop-2.test", message_id="<c@mail.example.test>", ref="x:3")
    Thread.objects.filter(pk=closed.thread_id).update(status=ThreadStatus.CLOSED)

    with mock.patch(NOTIFY):
        reply = ingest(channel, eml("reply_no_headers.eml"))

    assert (reply.thread_id, reply.matched_by, reply.message_id) == (newer.thread_id, ReplyMatch.SENDER, None)
    assert state_of(older).stopped_at is None


def test_C22_autoresponder_kind_auto_sequence_untouched_no_notify(channel):
    message = make_sent(channel, "owner@example-shop-3.test")

    with mock.patch(NOTIFY) as notify:
        reply = ingest(channel, eml("autoresponder.eml"))

    assert reply.kind == ReplyKind.AUTO
    assert Thread.objects.get(pk=message.thread_id).status == ThreadStatus.OPEN
    assert state_of(message).stopped_at is None and notify.call_count == 0


def test_classification_order_autoresponder_beats_optout(channel):
    make_sent(channel, "owner@example-shop-3.test", language="EN")
    raw = eml("autoresponder.eml").replace(b"I am out of the office", b"Unsubscribe. I am out of the office")

    with mock.patch(NOTIFY) as notify:
        assert ingest(channel, raw).kind == ReplyKind.AUTO
    assert notify.call_count == 0


@pytest.fixture
def optout_signal():
    calls = []

    def receiver(**kwargs):
        calls.append(kwargs)

    optout_confirmed.connect(receiver)
    yield calls
    optout_confirmed.disconnect(receiver)


def test_C23_optout_phrase_pauses_sequence_confirm_creates_suppression_and_signal(
    channel, optout_signal, django_capture_on_commit_callbacks
):
    message = make_sent(channel, "owner@example-shop-4.test")
    user = get_user_model().objects.create_user("sales")

    with mock.patch(NOTIFY) as notify:
        reply = ingest(channel, eml("optout_pl.eml"))

    assert reply.kind == ReplyKind.SUSPECTED_OPTOUT and state_of(message).stop_reason == SequenceStopReason.PAUSED
    assert Thread.objects.get(pk=message.thread_id).status == ThreadStatus.REPLIED
    assert notify.call_args.kwargs["severity"] == "medium" and not Suppression.objects.exists()

    with django_capture_on_commit_callbacks(execute=True):
        optout_service.confirm(reply, user=user)

    suppression = Suppression.objects.get()
    assert (suppression.value, suppression.reason, suppression.created_by) == (
        "owner@example-shop-4.test",
        "optout confirmed",
        user,
    )
    assert state_of(message).stop_reason == SequenceStopReason.OPTOUT
    expected = {"subject_ref": "leads.Company:1", "email": "owner@example-shop-4.test", "channel_idx": channel.idx}
    assert [{k: call[k] for k in expected} for call in optout_signal] == [expected]
    with pytest.raises(optout_service.OptoutStateError):
        optout_service.confirm(reply, user=user)


def test_optout_english_phrase_on_english_thread(channel):
    make_sent(channel, "owner@example-shop-5.test", language="EN")
    with mock.patch(NOTIFY):
        assert ingest(channel, eml("optout_en.eml")).kind == ReplyKind.SUSPECTED_OPTOUT


def test_optout_confirm_without_agreements_no_import_error(channel, django_capture_on_commit_callbacks):
    make_sent(channel, "owner@example-shop-4.test")
    with mock.patch(NOTIFY):
        reply = ingest(channel, eml("optout_pl.eml"))
    optout_service._objection_service.cache_clear()

    with django_capture_on_commit_callbacks(execute=True):
        optout_service.confirm(reply, user=None)

    assert optout_service._objection_service() is None
    optout_service._objection_service.cache_clear()


def test_optout_confirm_records_objection_when_agreements_installed(
    channel, monkeypatch, django_capture_on_commit_callbacks
):
    make_sent(channel, "owner@example-shop-4.test")
    with mock.patch(NOTIFY):
        reply = ingest(channel, eml("optout_pl.eml"))
    fake = SimpleNamespace(record_objection=mock.Mock())
    monkeypatch.setattr(optout_service, "_objection_service", lambda: fake)

    with django_capture_on_commit_callbacks(execute=True):
        optout_service.confirm(reply, user=None)

    fake.record_objection.assert_called_once_with(
        channel_idx=channel.idx, email="owner@example-shop-4.test", source="communicator", reason="reply opt-out"
    )


def test_C24_dsn_hard_bounce_failed_suppressed_sequence_stopped_notify_low(channel):
    message = make_sent(channel, "owner@example-shop-6.test")

    with mock.patch(NOTIFY) as notify:
        reply = ingest(channel, eml("dsn_hard.eml"))

    message.refresh_from_db()
    assert (reply.kind, reply.matched_by, reply.message_id) == (ReplyKind.BOUNCE_HARD, ReplyMatch.DSN, message.pk)
    assert (message.status, message.failure_code) == (MessageStatus.FAILED, "bounce")
    assert Suppression.objects.get().value == "owner@example-shop-6.test"
    assert state_of(message).stop_reason == SequenceStopReason.BOUNCE
    assert Thread.objects.get(pk=message.thread_id).status == ThreadStatus.OPEN
    assert notify.call_args.kwargs["severity"] == "low" and notify.call_args.kwargs["title"] == "Bounce"


def test_C25_dsn_soft_bounce_retry_after_24h_then_failed(channel):
    message = make_sent(channel, "owner@example-shop-6.test")

    ingest(channel, eml("dsn_soft.eml"))

    message.refresh_from_db()
    assert message.status == MessageStatus.SCHEDULED and message.scheduled_at == message.bounce_retry_at
    assert abs(message.bounce_retry_at - timezone.now() - timedelta(hours=24)) < timedelta(minutes=1)
    resent_id = "<communicator-1-resent00@mail.example.test>"
    message_service.transition(message, MessageStatus.SENT, fields={"message_id": resent_id})

    second = eml("dsn_soft.eml", resent_id).replace(b"<dsn-soft-1@", b"<dsn-soft-2@")
    ingest(channel, second)

    message.refresh_from_db()
    assert (message.status, message.failure_code) == (MessageStatus.FAILED, "bounce")
    assert Reply.objects.filter(kind=ReplyKind.BOUNCE_SOFT).count() == 2 and not Suppression.objects.exists()


def test_C26_duplicate_inbound_message_id_idempotent(channel):
    make_sent(channel, "owner@example-shop-1.test")

    with mock.patch(NOTIFY) as notify:
        first = ingest(channel, eml("reply_plain.eml"))
        again = ingest(channel, eml("duplicate.eml"))

    assert again.pk == first.pk and Reply.objects.count() == 1 and notify.call_count == 1
    assert "can we talk on Thursday" in Reply.objects.get().body_text


def test_own_outbound_mail_in_the_mailbox_is_ignored(channel):
    make_sent(channel, "owner@example-shop-1.test", message_id="<reply-plain-1@example-shop-1.test>")
    assert ingest(channel, eml("reply_plain.eml")) is None and not Reply.objects.exists()


class FakeImap:
    """Enough of imaplib.IMAP4 for the poll: UID SEARCH n:* (always includes the highest UID), FETCH."""

    def __init__(self, mails: dict[int, bytes]):
        self.mails, self.login_error = mails, None

    def __call__(self, host, port, timeout):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def shutdown(self):
        pass

    def login(self, user, password):
        if self.login_error:
            raise self.login_error

    def select(self, folder, readonly=False):
        assert readonly, "the poll must never change flags"
        return "OK", [str(len(self.mails)).encode()]

    def uid(self, command, *args):
        if command == "SEARCH":
            low = int(args[1].split()[1].split(":")[0])
            uids = {uid for uid in self.mails if uid >= low} | ({max(self.mails)} if self.mails else set())
            return "OK", [b" ".join(str(uid).encode() for uid in sorted(uids))]
        raw = self.mails[int(args[0])]
        return "OK", [(f"{args[0]} (UID {args[0]} BODY[] {{{len(raw)}}}".encode(), raw), b")"]


@pytest.fixture
def mailbox(channel):
    return MailboxConfig.objects.create(
        channel=channel,
        imap_host="imap.test",
        imap_port=143,
        imap_use_ssl=False,
        imap_user="u",
        imap_password="p",  # noqa: S106
    )


def test_unmatched_mail_dropped_cursor_advances(channel, mailbox):
    make_sent(channel, "owner@example-shop-1.test")
    fake = FakeImap({3: eml("reply_no_headers.eml"), 5: eml("reply_plain.eml")})

    with mock.patch.object(imaplib, "IMAP4", fake), mock.patch(NOTIFY):
        first = poll_service.poll_all()
        second = poll_service.poll_all()

    mailbox.refresh_from_db()
    assert (dict(first), dict(second)) == ({"ingested": 1, "skipped": 1}, {"ingested": 0, "skipped": 0})
    assert (mailbox.last_uid, Reply.objects.count()) == (5, 1) and mailbox.last_polled_at is not None


def test_uid_restart_below_cursor_resets_it(channel, mailbox):
    make_sent(channel, "owner@example-shop-1.test")
    MailboxConfig.objects.filter(pk=mailbox.pk).update(last_uid=40)

    with mock.patch.object(imaplib, "IMAP4", FakeImap({1: eml("reply_plain.eml")})), mock.patch(NOTIFY):
        assert poll_service.poll_all()["ingested"] == 1


def test_imap_error_raises_for_retry_and_notifies_once_a_day(channel, mailbox):
    fake = FakeImap({})
    fake.login_error = IMAP_ERROR("auth failed")

    with mock.patch.object(imaplib, "IMAP4", fake), mock.patch(NOTIFY) as notify:
        for _ in range(2):
            with pytest.raises(IMAP_ERROR):
                poll_service.poll_all()

    assert notify.call_count == 1 and notify.call_args.kwargs["title"] == "IMAP poll failed"


def test_mailbox_password_encrypted_and_write_only(channel, admin_api):
    body = {"imap_host": "imap.test", "imap_port": 143, "imap_use_ssl": False, "imap_user": "u"}

    created = admin_api.put(api_url("mailbox/"), {**body, "imap_password": "imap-pass-1"}, format="json")
    kept = admin_api.put(api_url("mailbox/"), {**body, "folder": "Replies"}, format="json")

    assert created.status_code == kept.status_code == 200
    assert "imap_password" not in kept.json() and kept.json()["has_password"] is True
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT imap_password FROM {MailboxConfig._meta.db_table}")  # noqa: S608 — table name
        stored = cursor.fetchone()[0]
    assert stored and "imap-pass-1" not in stored
    assert MailboxConfig.objects.get().imap_password == "imap-pass-1"
    assert "imap-pass-1" not in admin_api.get(api_url("mailbox/")).content.decode()


def test_poll_now_absent_outside_development(channel, admin_api, once_backend):
    assert admin_api.post(api_url("test/poll-now/")).json() == {"ingested": 0, "skipped": 0}
    with override_settings(ENVIRONMENT="production"):
        assert admin_api.post(api_url("test/poll-now/")).status_code == 404


def test_optout_actions_api(channel, admin_api):
    make_sent(channel, "owner@example-shop-4.test")
    with mock.patch(NOTIFY):
        suspicion = ingest(channel, eml("optout_pl.eml"))
        auto = ingest(channel, eml("autoresponder.eml"))

    assert admin_api.post(api_url(f"replies/{auto.pk}/confirm-optout/")).status_code == 409
    assert admin_api.post(api_url(f"replies/{suspicion.pk}/dismiss-optout/")).json()["kind"] == "reply"
    assert admin_api.post(api_url(f"replies/{suspicion.pk}/confirm-optout/")).status_code == 409
    assert admin_api.post(api_url("replies/999999/confirm-optout/")).status_code == 404
    listed = admin_api.get(api_url("replies/?kind=auto")).json()
    assert [row["id"] for row in listed["results"]] == [auto.pk]


def test_confirm_optout_api_suppresses(channel, admin_api):
    make_sent(channel, "owner@example-shop-4.test")
    with mock.patch(NOTIFY):
        suspicion = ingest(channel, eml("optout_pl.eml"))

    response = admin_api.post(api_url(f"replies/{suspicion.pk}/confirm-optout/"))

    assert response.status_code == 200 and response.json()["optout_confirmed_at"]
    values = [row["value"] for row in admin_api.get(api_url("suppressions/")).json()["results"]]
    assert values == ["owner@example-shop-4.test"]


def test_threads_list_filters_by_subject_ref(channel, admin_api):
    make_sent(channel, "a@example-shop-1.test", message_id="<a@x.test>", ref="leads.Company:1")
    make_sent(channel, "b@example-shop-1.test", message_id="<b@x.test>", ref="leads.Company:2")

    rows = admin_api.get(api_url("threads/?subject_ref=leads.Company:2")).json()["results"]

    assert [row["recipient_email"] for row in rows] == ["b@example-shop-1.test"]


def test_thread_timeline_in_at_most_4_queries(channel, admin_api, django_assert_max_num_queries):
    message = make_sent(channel, "owner@example-shop-1.test")
    with mock.patch(NOTIFY):
        ingest(channel, eml("reply_plain.eml"))
    url = api_url(f"threads/{message.thread_id}/")

    with django_assert_max_num_queries(4):
        response = admin_api.get(url)

    timeline = response.json()["timeline"]
    assert [(e["kind"], e["reply_kind"]) for e in timeline] == [("message", ""), ("reply", "reply")]
    assert admin_api.get(api_url("threads/999999/")).status_code == 404


def test_inbound_admin_pages_render(client, channel, mailbox):
    client.force_login(get_user_model().objects.create_superuser("root", "root@example.test", "pw"))
    for url in ("mailboxconfig", "reply", f"mailboxconfig/{mailbox.pk}/change"):
        assert client.get(f"/admin/django_communicator/{url}/").status_code == 200
