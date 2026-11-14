# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Mailboxes: resolution of a thread's mailbox, CRUD over the admin API, write-only passwords, state, login test."""

import imaplib
import smtplib
import ssl
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from django_communicator.enums import MailboxState, MessageStatus
from django_communicator.models import Mailbox, Message, Thread
from django_communicator.services import mailbox_service, review_service
from django_communicator.services.communicate_service import RecipientData, communicate
from tests.conftest import CHANNEL_IDX, api_url
from tests.factories import MAILBOX_SECRET, ChannelFactory, make_mailbox, make_static

IMAP_ERROR = imaplib.IMAP4.error
NON_ASCII = "zażółć-1"  # a password smtplib could never send


def staff(username: str):
    return get_user_model().objects.create_user(username=username, is_staff=True)


def draft(email: str = "jan@example-shop-1.test", *, user_id: int | None = None, ref: str = "mb:1"):
    recipient = RecipientData(email=email, first_name="Jan", language="pl")
    return communicate(
        channel_idx=CHANNEL_IDX,
        template_key="followup",
        recipient=recipient,
        context={},
        subject_ref=ref,
        mailbox_user_id=user_id,
    )


@pytest.fixture
def template(channel):
    return make_static(channel)


def raw_password(mailbox: Mailbox, column: str = "smtp_password") -> str:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM {Mailbox._meta.db_table} WHERE id = %s", [mailbox.pk])  # noqa: S608
        return cursor.fetchone()[0]


# --- resolution: user → default → none, stored once on the thread ---


def test_resolution_user_then_default_then_none(channel):
    anna, piotr, ewa = staff("anna"), staff("piotr"), staff("ewa")
    own = make_mailbox(channel, user=anna)
    make_mailbox(channel, "off@mail.example.test", user=ewa, send_enabled=False)
    assert mailbox_service.resolve(channel, anna.pk) == own
    assert mailbox_service.resolve(channel, piotr.pk) is None  # no own mailbox, no default
    default = make_mailbox(channel, "team@mail.example.test", is_default=True)
    assert mailbox_service.resolve(channel, piotr.pk) == default
    assert mailbox_service.resolve(channel, ewa.pk) == default  # own mailbox does not send
    assert mailbox_service.resolve(channel, None) == default
    Mailbox.objects.filter(pk=default.pk).update(send_enabled=False)
    assert mailbox_service.resolve(channel, None) is None  # a default that does not send is no default


def test_thread_mailbox_set_by_the_first_message_and_kept(channel, template):
    anna, piotr = staff("anna"), staff("piotr")
    first = make_mailbox(channel, user=anna)
    make_mailbox(channel, "piotr@mail.example.test", user=piotr)

    message = draft(user_id=anna.pk)
    again = draft(user_id=piotr.pk)

    assert message.thread_id == again.thread_id
    assert Thread.objects.get(pk=message.thread_id).mailbox_id == first.pk


def test_thread_without_mailbox_stays_without_one(channel, template):
    message = draft()
    make_mailbox(channel, is_default=True)

    draft()

    assert Thread.objects.get(pk=message.thread_id).mailbox_id is None


# --- admin API ---


def test_create_list_and_passwords_write_only(channel, admin_api):
    body = {"address": "anna@mail.example.test", "smtp_host": "smtp.test", "smtp_password": MAILBOX_SECRET}
    created = admin_api.post(api_url("mailboxes/"), {**body, "imap_password": MAILBOX_SECRET}, format="json")
    listed = admin_api.get(api_url("mailboxes/"))

    assert created.status_code == 201
    row = created.json()
    assert (row["smtp_password_set"], row["imap_password_set"], row["state"]) == (True, True, "disabled")
    assert [mailbox["id"] for mailbox in listed.json()["results"]] == [row["id"]]
    mailbox = Mailbox.objects.get()
    assert MAILBOX_SECRET not in raw_password(mailbox) and MAILBOX_SECRET not in raw_password(mailbox, "imap_password")
    assert mailbox.smtp_password == MAILBOX_SECRET
    for response in (created, listed, admin_api.get(api_url(f"mailboxes/{mailbox.pk}/"))):
        assert MAILBOX_SECRET not in response.content.decode() and 'smtp_password"' not in response.content.decode()


def test_patch_keeps_passwords_and_moves_the_default(channel, admin_api):
    old = make_mailbox(channel, is_default=True)
    new = make_mailbox(channel, "team@mail.example.test")

    response = admin_api.patch(api_url(f"mailboxes/{new.pk}/"), {"is_default": True, "smtp_password": None}, "json")

    old.refresh_from_db()
    new.refresh_from_db()
    assert response.status_code == 200 and response.json()["is_default"] is True
    assert (old.is_default, new.is_default, new.smtp_password) == (False, True, MAILBOX_SECRET)


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"address": "anna@mail.example.test"}, 400),  # taken address
        ({"address": "new@mail.example.test", "warmup_start_cap": 5}, 400),  # warm-up without a ceiling
        ({"address": "new@mail.example.test", "role": "x"}, 400),  # unknown field
        ({"address": "not-an-address"}, 400),
    ],
)
def test_create_refusals(channel, admin_api, body, status):
    make_mailbox(channel)
    assert admin_api.post(api_url("mailboxes/"), body, format="json").status_code == status


def test_user_must_be_staff_and_unique_per_channel(channel, admin_api):
    customer = get_user_model().objects.create_user(username="customer-1")
    anna = staff("anna")
    make_mailbox(channel, user=anna)
    url = api_url("mailboxes/")

    assert admin_api.post(url, {"address": "c@mail.example.test", "user_id": customer.pk}, "json").status_code == 400
    assert admin_api.post(url, {"address": "b@mail.example.test", "user_id": anna.pk}, "json").status_code == 400


def test_warmup_starts_today_when_no_date_given(channel, admin_api, policy):
    body = {"address": "new@mail.example.test", "daily_cap": 40, "warmup_start_cap": 5, "warmup_step": 5}
    assert admin_api.post(api_url("mailboxes/"), body, format="json").json()["warmup_started_on"] == "2026-09-14"


def test_delete_refused_for_default_and_used_mailbox(channel, admin_api, template):
    default = make_mailbox(channel, is_default=True)
    used = make_mailbox(channel, "used@mail.example.test", user=staff("anna"))
    free = make_mailbox(channel, "free@mail.example.test")
    draft(user_id=used.user_id)

    assert admin_api.delete(api_url(f"mailboxes/{default.pk}/")).status_code == 409
    assert admin_api.delete(api_url(f"mailboxes/{used.pk}/")).status_code == 409
    assert admin_api.delete(api_url(f"mailboxes/{free.pk}/")).status_code == 204
    assert set(Mailbox.objects.values_list("pk", flat=True)) == {default.pk, used.pk}


def test_mailbox_of_another_channel_is_404_and_customer_is_403(channel, admin_api, customer_api):
    other = make_mailbox(ChannelFactory(idx="second-channel", label="Second"))
    assert admin_api.get(api_url(f"mailboxes/{other.pk}/")).status_code == 404
    assert customer_api.get(api_url("mailboxes/")).status_code == 403


def test_old_singular_route_is_gone(channel, admin_api):
    assert admin_api.get(api_url("mailbox/")).status_code == 404


# --- undecryptable password ---


def test_undecryptable_password_never_overwritten_by_an_unrelated_save(channel, admin_api):
    mailbox = make_mailbox(channel)
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {Mailbox._meta.db_table} SET smtp_password = %s WHERE id = %s", ["old-key", mailbox.pk])  # noqa: S608

    response = admin_api.patch(api_url(f"mailboxes/{mailbox.pk}/"), {"display_name": "Anna"}, format="json")
    mailbox_service.update_mailbox(Mailbox.objects.get(pk=mailbox.pk), {"daily_cap": 9})

    assert response.json()["state"] == MailboxState.NEEDS_LOGIN and response.json()["smtp_password_set"] is False
    assert raw_password(mailbox) == "old-key"


def test_new_password_replaces_and_clears_needs_login(channel, admin_api):
    mailbox = make_mailbox(channel, smtp_login_failed_at="2026-09-14T08:00:00Z")
    assert mailbox_service.state(mailbox) == MailboxState.NEEDS_LOGIN

    body = admin_api.patch(api_url(f"mailboxes/{mailbox.pk}/"), {"smtp_password": "new-one-1"}, format="json").json()

    assert (body["state"], body["smtp_login_failed_at"]) == (MailboxState.ACTIVE, None)


def test_states(channel):
    assert mailbox_service.state(make_mailbox(channel)) == MailboxState.ACTIVE
    assert mailbox_service.state(make_mailbox(channel, "b@x.test", send_enabled=False)) == MailboxState.DISABLED
    assert mailbox_service.state(make_mailbox(channel, "c@x.test", smtp_password="")) == MailboxState.NEEDS_LOGIN
    receiving = make_mailbox(channel, "d@x.test", send_enabled=False, receive_enabled=True, imap_host="imap.test")
    assert mailbox_service.state(receiving) == MailboxState.NEEDS_LOGIN
    paused = make_mailbox(channel, "e@x.test", paused_at="2026-09-14T08:00:00Z", smtp_password="")
    assert mailbox_service.state(paused) == MailboxState.PAUSED


# --- login test ---


class FakeSmtp:
    def __init__(self, error: Exception | None = None):
        self.error, self.logins = error, []

    def __call__(self, host, port, **kwargs):
        return self

    def ehlo(self, *args):
        return 250, b"ok"

    def starttls(self, *args, **kwargs):
        return 220, b"ok"

    def login(self, user, password):
        self.logins.append(user)
        if self.error:
            raise self.error

    def quit(self):
        pass

    def close(self):
        pass


class FakeImapLogin:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def __call__(self, host, port, timeout):
        return self

    def login(self, user, password):
        if self.error:
            raise self.error

    def logout(self):
        pass

    def shutdown(self):
        pass


SMTP_EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


@pytest.fixture
def host_smtp(settings):
    """The host's mail backend is the one a send uses; pytest-django replaces it with locmem."""
    settings.EMAIL_BACKEND = SMTP_EMAIL_BACKEND


def test_login_test_uses_the_hosts_mail_backend(channel, settings):
    mailbox = make_mailbox(channel)
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    with mock.patch.object(smtplib, "SMTP", FakeSmtp(smtplib.SMTPAuthenticationError(535, b"bad"))):
        assert mailbox_service.check_login(mailbox)["smtp"] == "ok"  # no fixed backend: the host's answers


def test_a_certificate_the_host_refuses_is_its_own_answer(channel, host_smtp):
    mailbox = make_mailbox(channel, imap_host="imap.test", imap_password=MAILBOX_SECRET)
    refused = ssl.SSLCertVerificationError("certificate verify failed")

    with (
        mock.patch.object(smtplib, "SMTP", FakeSmtp(refused)),
        mock.patch.object(imaplib, "IMAP4_SSL", mock.Mock(side_effect=refused)),
    ):
        assert mailbox_service.check_login(mailbox) == {"smtp": "certificate", "imap": "certificate"}


def test_a_server_that_does_not_answer_is_unreachable(channel, host_smtp):
    mailbox = make_mailbox(channel, imap_host="imap.test", imap_password=MAILBOX_SECRET)
    silent = ConnectionRefusedError("refused")

    with (
        mock.patch.object(smtplib, "SMTP", mock.Mock(side_effect=silent)),
        mock.patch.object(imaplib, "IMAP4_SSL", mock.Mock(side_effect=silent)),
    ):
        assert mailbox_service.check_login(mailbox) == {"smtp": "unreachable", "imap": "unreachable"}


def test_login_test_endpoint(channel, admin_api, host_smtp):
    mailbox = make_mailbox(channel, imap_host="imap.test", imap_password=MAILBOX_SECRET, smtp_login_failed_at=None)
    Mailbox.objects.filter(pk=mailbox.pk).update(smtp_login_failed_at="2026-09-14T08:00:00Z")
    smtp, imap = FakeSmtp(), FakeImapLogin(IMAP_ERROR("no"))

    with mock.patch.object(smtplib, "SMTP", smtp), mock.patch.object(imaplib, "IMAP4_SSL", imap):
        response = admin_api.post(api_url(f"mailboxes/{mailbox.pk}/test/"))

    mailbox.refresh_from_db()
    assert response.json() == {"smtp": "ok", "imap": "auth_failed"}
    assert smtp.logins == ["anna@mail.example.test"] and mailbox.smtp_login_failed_at is None


def test_login_test_codes_without_credentials_or_with_refused_login(channel, host_smtp):
    bare = make_mailbox(channel, "bare@x.test", smtp_host="", smtp_password="")
    refused = make_mailbox(channel, "refused@x.test", imap_host="imap.test")
    smtp = FakeSmtp(smtplib.SMTPAuthenticationError(535, b"bad"))

    with mock.patch.object(smtplib, "SMTP", smtp):
        assert mailbox_service.check_login(bare) == {"smtp": "not_configured", "imap": "not_configured"}
        assert mailbox_service.check_login(refused) == {"smtp": "auth_failed", "imap": "no_password"}


# --- approval inbox ---


def test_approval_filter_counts_and_blocker_for_a_paused_mailbox(channel, admin_api, template):
    anna = staff("anna")
    own = make_mailbox(channel, user=anna)
    mine, other = draft(user_id=anna.pk), draft("ewa@example-shop-2.test", ref="mb:2")
    Mailbox.objects.filter(pk=own.pk).update(paused_at="2026-09-14T08:00:00Z")

    filtered = admin_api.get(f"/api/communicator/v2/admin/approvals/?mailbox={own.pk}").json()
    everything = admin_api.get("/api/communicator/v2/admin/approvals/").json()

    assert [row["id"] for row in filtered["results"]] == [mine.pk]
    assert "paused" in filtered["results"][0]["blocker"]
    assert everything["counts"]["by_mailbox"] == {str(own.pk): 1, "none": 1}
    with pytest.raises(review_service.ReviewError):
        review_service.accept(Message.objects.get(pk=mine.pk), user=anna)
    assert review_service.accept(other, user=anna).status == MessageStatus.APPROVED


# --- Django admin ---


@pytest.fixture
def root_client(client, db):
    client.force_login(get_user_model().objects.create_superuser("root", "root@example.test", "pw"))
    return client


def _admin_form(mailbox: Mailbox, **overrides) -> dict:
    fields = {
        "address": mailbox.address, "display_name": "", "cc_address": "", "user": "", "auth_method": "password",
        "smtp_host": mailbox.smtp_host, "smtp_port": 587, "smtp_security": "starttls", "smtp_user": "",
        "smtp_password": "", "send_enabled": "on", "imap_host": "", "imap_port": 993, "folder": "INBOX",
        "daily_cap": "", "warmup_start_cap": "", "warmup_step": 0, "warmup_started_on": "",
    }  # fmt: skip
    return {**fields, **overrides}


def test_admin_moves_the_default_and_keeps_the_password(channel, root_client):
    old = make_mailbox(channel, is_default=True)
    new = make_mailbox(channel, "team@mail.example.test")
    url = f"/admin/django_communicator/mailbox/{new.pk}/change/"

    response = root_client.post(url, _admin_form(new, is_default="on"))

    old.refresh_from_db()
    new.refresh_from_db()
    assert response.status_code == 302
    assert (old.is_default, new.is_default, new.smtp_password) == (False, True, MAILBOX_SECRET)


def test_admin_shows_service_refusals_as_form_errors(channel, root_client):
    mailbox = make_mailbox(channel)
    url = f"/admin/django_communicator/mailbox/{mailbox.pk}/change/"

    warmup = root_client.post(url, _admin_form(mailbox, warmup_start_cap=5))
    accented = root_client.post(url, _admin_form(mailbox, smtp_password=NON_ASCII))

    assert warmup.status_code == accented.status_code == 200  # the form again, with the error
    assert "daily_cap" in warmup.content.decode() and "ASCII" in accented.content.decode()
    mailbox.refresh_from_db()
    assert (mailbox.warmup_start_cap, mailbox.smtp_password) == (None, MAILBOX_SECRET)


def test_non_ascii_password_refused_by_the_api(channel, admin_api):
    body = {"address": "anna@mail.example.test", "smtp_password": NON_ASCII}
    assert admin_api.post(api_url("mailboxes/"), body, format="json").status_code == 400
