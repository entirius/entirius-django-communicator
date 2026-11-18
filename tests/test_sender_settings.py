# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Sender-side settings (U20–U25): several mailboxes per user with a default and a brand, CC/BCC lists, signature,
footers per brand, the unsubscribe link text and placeholder, and the order body → signature → footer → link."""

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils.html import escape

from django_communicator import settings as communicator_settings
from django_communicator.enums import ChannelMode
from django_communicator.models import Mailbox, MailFooter, Message
from django_communicator.services import (
    channel_service,
    footer_service,
    inbound_service,
    mail_builder,
    mailbox_service,
    send_service,
    unsubscribe_service,
)
from django_communicator.services.communicate_service import RecipientData, communicate
from django_communicator.services.compose_service import compose
from tests.conftest import CHANNEL_IDX, api_url
from tests.factories import LanguageFactory, make_mailbox

APP = "django_communicator"
BASE = "https://api.example.test"
EMAIL = "jan@example-shop-1.test"
LINK_STYLE = "color:#555555"
PARAGRAPH_STYLE = "font-family:Arial,sans-serif;font-size:11px;color:#555555"
PL_LABEL = "Nie chcę otrzymywać kolejnych wiadomości"


def staff(username: str):
    return get_user_model().objects.create_user(username=username, is_staff=True)


def message_from(user_id: int | None = None, brand: str = "", ref: str = "ss:1", legal: str = "Legal.") -> Message:
    recipient = RecipientData(email=EMAIL, first_name="Jan", language="pl", legal_footer=legal)
    return communicate(
        channel_idx=CHANNEL_IDX,
        template_key="followup",
        recipient=recipient,
        context={},
        subject_ref=ref,
        requires_review=False,
        mailbox_user_id=user_id,
        mailbox_brand=brand,
    )


def parts(message: Message) -> tuple[str, str]:
    built = mail_builder.build(Message.objects.get(pk=message.pk)).message()
    return tuple(part.get_payload(decode=True).decode().rstrip("\n") for part in built.get_payload())


@pytest.fixture
def unsubscribe_on(monkeypatch):
    monkeypatch.setattr(communicator_settings, "COMMUNICATOR_UNSUBSCRIBE_BASE_URL", BASE)


def link() -> str:
    return unsubscribe_service.url_for(CHANNEL_IDX, EMAIL)


# --- mailbox fields over the admin API ---


def test_copy_lists_are_trimmed_lowered_deduplicated_and_replaced_as_a_whole(channel, admin_api):
    body = {
        "address": "anna@mail.example.test",
        "cc_addresses": [" CRM@Mail.Example.test", "crm@mail.example.test", "boss@mail.example.test"],
        "bcc_addresses": ["Archive@mail.example.test"],
    }
    created = admin_api.post(api_url("mailboxes/"), body, format="json").json()
    assert (created["cc_addresses"], created["bcc_addresses"]) == (
        ["crm@mail.example.test", "boss@mail.example.test"],
        ["archive@mail.example.test"],
    )
    assert "cc_address" not in created

    patched = admin_api.patch(api_url(f"mailboxes/{created['id']}/"), {"cc_addresses": []}, "json").json()

    assert (patched["cc_addresses"], patched["bcc_addresses"]) == ([], ["archive@mail.example.test"])


@pytest.mark.parametrize(
    "changes",
    [
        {"cc_addresses": ["not-an-address"]},
        {"bcc_addresses": [f"a{n}@mail.example.test" for n in range(11)]},
        {"brand": "Ecom House"},
        {"brand": "x" * 33},
        {"cc_address": "old@mail.example.test"},  # the single field is gone
    ],
)
def test_invalid_copies_brand_or_the_old_field_are_400(channel, admin_api, changes):
    mailbox = make_mailbox(channel)

    assert admin_api.patch(api_url(f"mailboxes/{mailbox.pk}/"), changes, "json").status_code == 400


def test_eleven_addresses_that_collapse_to_ten_are_accepted(channel):
    mailbox = make_mailbox(channel)
    addresses = [f"a{n}@mail.example.test" for n in range(10)] + ["A0@mail.example.test"]

    assert len(mailbox_service.update_mailbox(mailbox, {"cc_addresses": addresses}).cc_addresses) == 10


def test_brand_and_signature_are_stored_cleaned(channel, admin_api):
    mailbox = make_mailbox(channel)
    signature = '<p onclick="x()">Anna <strong>Nowak</strong></p><script>alert(1)</script>'

    row = admin_api.patch(
        api_url(f"mailboxes/{mailbox.pk}/"), {"brand": "ecom.house", "signature_html": signature}, "json"
    ).json()

    assert (row["brand"], row["signature_html"]) == ("ecom.house", "<p>Anna <strong>Nowak</strong></p>")
    assert mailbox_service.update_mailbox(mailbox, {"brand": " Entirius "}).brand == "entirius"
    upper = admin_api.patch(api_url(f"mailboxes/{mailbox.pk}/"), {"brand": "Ecom.House"}, "json")
    assert upper.json()["brand"] == "ecom.house"  # lower-cased before the slug check, as leads does


@pytest.mark.parametrize("signature", ["<p>{{ legal }}</p>", "<p>{{unsubscribe}}</p>"])
def test_a_signature_with_a_placeholder_is_refused(channel, admin_api, signature):
    mailbox = make_mailbox(channel)

    response = admin_api.patch(api_url(f"mailboxes/{mailbox.pk}/"), {"signature_html": signature}, "json")

    assert response.status_code == 400
    mailbox.refresh_from_db()
    assert mailbox.signature_html == ""


# --- the default of a user ---


def test_first_mailbox_of_a_user_is_the_default_and_setting_another_moves_it(channel):
    anna = staff("anna")
    first = mailbox_service.create_mailbox(channel, {"address": "a1@mail.example.test", "user_id": anna.pk})
    second = mailbox_service.create_mailbox(channel, {"address": "a2@mail.example.test", "user_id": anna.pk})
    nobody = mailbox_service.create_mailbox(channel, {"address": "team@mail.example.test", "is_user_default": True})
    assert (first.is_user_default, second.is_user_default, nobody.is_user_default) == (True, False, False)

    mailbox_service.update_mailbox(second, {"is_user_default": True})

    first.refresh_from_db()
    assert (first.is_user_default, second.is_user_default) == (False, True)


def test_deleting_or_moving_the_users_default_promotes_the_oldest_other(channel):
    anna, piotr = staff("anna"), staff("piotr")
    first = mailbox_service.create_mailbox(channel, {"address": "a1@mail.example.test", "user_id": anna.pk})
    second = mailbox_service.create_mailbox(channel, {"address": "a2@mail.example.test", "user_id": anna.pk})
    third = mailbox_service.create_mailbox(channel, {"address": "a3@mail.example.test", "user_id": anna.pk})

    mailbox_service.update_mailbox(first, {"user_id": piotr.pk})  # Piotr had none: it stays a default, his
    second.refresh_from_db()
    assert (first.is_user_default, second.is_user_default) == (True, True)

    mailbox_service.delete_mailbox(second)
    third.refresh_from_db()
    assert third.is_user_default is True

    mailbox_service.update_mailbox(third, {"user_id": piotr.pk})  # Piotr has a default: it does not take it
    first.refresh_from_db()
    assert (first.is_user_default, third.is_user_default) == (True, False)

    mailbox_service.update_mailbox(first, {"user_id": None})
    third.refresh_from_db()
    assert (first.is_user_default, third.is_user_default) == (False, True)


def test_admin_form_takes_address_lines_brand_and_the_user_default(channel, client):
    client.force_login(get_user_model().objects.create_superuser("root", "root@example.test", "pw"))
    mailbox = make_mailbox(channel)
    form = {
        "address": mailbox.address, "display_name": "", "cc_addresses": "CRM@mail.example.test\nboss@mail.example.test",
        "bcc_addresses": "archive@mail.example.test", "brand": "ecom.house", "signature_html": "<p>Anna</p>",
        "user": "", "auth_method": "password", "smtp_host": mailbox.smtp_host, "smtp_port": 587,
        "smtp_security": "starttls", "smtp_user": "", "smtp_password": "", "send_enabled": "on", "imap_host": "",
        "imap_port": 993, "folder": "INBOX", "daily_cap": "", "warmup_start_cap": "", "warmup_step": 0,
        "warmup_started_on": "",
    }  # fmt: skip

    response = client.post(f"/admin/django_communicator/mailbox/{mailbox.pk}/change/", form)

    assert response.status_code == 302
    mailbox.refresh_from_db()
    assert mailbox.cc_addresses == ["crm@mail.example.test", "boss@mail.example.test"]
    assert (mailbox.bcc_addresses, mailbox.brand) == (["archive@mail.example.test"], "ecom.house")
    page = client.get(f"/admin/django_communicator/mailbox/{mailbox.pk}/change/").content.decode()
    assert "crm@mail.example.test\nboss@mail.example.test" in page and "is_user_default" in page


# --- which mailbox sends ---


def test_resolution_brand_then_user_default_then_any_then_channel_default(channel):
    anna, piotr = staff("anna"), staff("piotr")
    oldest = make_mailbox(channel, "a1@mail.example.test", user=anna, brand="entirius")
    default = make_mailbox(channel, "a2@mail.example.test", user=anna, is_user_default=True)
    ecom_old = make_mailbox(channel, "a3@mail.example.test", user=anna, brand="ecom.house")
    ecom_default = make_mailbox(channel, "a4@mail.example.test", user=anna, brand="ecom.house")
    team = make_mailbox(channel, "team@mail.example.test", is_default=True, brand="ecom.house")

    assert mailbox_service.resolve(channel, anna.pk, "entirius") == oldest
    assert mailbox_service.resolve(channel, anna.pk, "ecom.house") == ecom_old  # several: the oldest
    Mailbox.objects.filter(pk=default.pk).update(is_user_default=False)
    Mailbox.objects.filter(pk=ecom_default.pk).update(is_user_default=True)
    assert mailbox_service.resolve(channel, anna.pk, "ecom.house") == ecom_default  # the user's default first
    assert mailbox_service.resolve(channel, anna.pk, "other") == ecom_default  # no such brand: the user's default
    assert mailbox_service.resolve(channel, anna.pk) == ecom_default
    Mailbox.objects.filter(pk=ecom_default.pk).update(is_user_default=False)
    assert mailbox_service.resolve(channel, anna.pk, "other") == oldest  # no default: the oldest
    assert mailbox_service.resolve(channel, piotr.pk, "ecom.house") == team  # no own mailbox: the channel default
    assert mailbox_service.resolve(channel, None, "ecom.house") == team
    Mailbox.objects.filter(pk=oldest.pk).update(send_enabled=False)
    assert mailbox_service.resolve(channel, anna.pk, "entirius") == default  # only sending mailboxes count


def test_communicate_and_compose_pick_the_brand_mailbox_and_the_thread_keeps_it(channel, static_template, policy):
    anna = staff("anna")
    make_mailbox(channel, "a1@mail.example.test", user=anna, is_user_default=True)
    ecom = make_mailbox(channel, "a2@mail.example.test", user=anna, brand="ecom.house")

    first = message_from(anna.pk, "ecom.house")
    later = message_from(anna.pk, "", ref="ss:1")  # the same thread: it keeps its mailbox
    recipient = RecipientData(email="ewa@example-shop-2.test", language="pl", legal_footer="Legal.")
    written = compose(
        channel_idx=CHANNEL_IDX,
        recipient=recipient,
        subject="Hello",
        body_text="Hi Ewa",
        subject_ref="ss:2",
        author=anna,
        mailbox_user_id=anna.pk,
        mailbox_brand="ecom.house",
    )

    assert first.thread.mailbox_id == ecom.pk and later.thread_id == first.thread_id
    assert Message.objects.get(pk=later.pk).thread.mailbox_id == ecom.pk
    assert written.thread.mailbox_id == ecom.pk


def test_dev_endpoints_take_the_mailbox_brand(channel, static_template, admin_api):
    anna = staff("anna")
    ecom = make_mailbox(channel, "a2@mail.example.test", user=anna, brand="ecom.house")
    recipient = {"email": EMAIL, "language": "pl", "legal_footer": "Legal."}
    body = {"template_key": "followup", "recipient": recipient, "subject_ref": "dev:1"}

    response = admin_api.post(
        api_url("test/communicate/"), {**body, "mailbox_user_id": anna.pk, "mailbox_brand": "ecom.house"}, "json"
    )
    refused = admin_api.post(api_url("test/communicate/"), {**body, "mailbox_brand": "Ecom House"}, "json")

    assert response.status_code == 201, response.content
    assert Message.objects.get(pk=response.json()["id"]).thread.mailbox_id == ecom.pk
    assert refused.status_code == 400


# --- the mail: copies, signature, order ---


def test_cc_and_bcc_leave_live_and_bcc_is_never_a_header(channel, static_template, policy):
    anna = staff("anna")
    make_mailbox(
        channel, user=anna, cc_addresses=["crm@mail.example.test", "boss@mail.example.test"],
        bcc_addresses=["archive@mail.example.test"],
    )  # fmt: skip
    message = message_from(anna.pk)

    built = mail_builder.build(Message.objects.get(pk=message.pk))

    assert built.cc == ["crm@mail.example.test", "boss@mail.example.test"]
    assert built.bcc == ["archive@mail.example.test"]
    assert "archive@mail.example.test" in built.recipients()
    assert "archive@mail.example.test" not in built.message().as_string()


def test_sandbox_names_cc_and_bcc_in_headers_only(channel, static_template, policy):
    channel_service.set_mode(channel, mode=ChannelMode.SANDBOX, sandbox_mailbox="sandbox@mail.example.test")
    anna = staff("anna")
    make_mailbox(
        channel, user=anna, cc_addresses=["crm@mail.example.test", "boss@mail.example.test"],
        bcc_addresses=["archive@mail.example.test"],
    )  # fmt: skip
    message_from(anna.pk)

    send_service.run_send_due()

    (sent,) = mail.outbox
    assert (sent.cc, sent.bcc, sent.recipients()) == ([], [], ["sandbox@mail.example.test"])
    assert sent.extra_headers["X-Original-Cc"] == "crm@mail.example.test, boss@mail.example.test"
    assert sent.extra_headers["X-Original-Bcc"] == "archive@mail.example.test"


def test_order_is_body_signature_footer_link(channel, static_template, policy, unsubscribe_on):
    anna = staff("anna")
    make_mailbox(channel, user=anna, signature_html="<p>Anna Nowak<br>Sales</p>")
    footer_service.save_footer(channel, LanguageFactory(iso2="PL"), "<div>Company</div>{{ legal }}")

    text, html = parts(message_from(anna.pk))

    expected = (
        f'<p>Following up.</p><p>Anna Nowak<br>Sales</p><div>Company</div><p>Legal.</p><p style="{PARAGRAPH_STYLE}">'
        f'<a href="{escape(link())}" style="{LINK_STYLE}">{PL_LABEL}</a></p>'
    )
    assert html == expected
    assert text == f"Following up.\n\nAnna Nowak\nSales\n\n-- \nCompany\n\nLegal.\n\n{PL_LABEL} <{link()}>"


def test_without_footer_the_signature_still_precedes_legal_text_and_link(
    channel, static_template, policy, unsubscribe_on
):
    anna = staff("anna")
    make_mailbox(channel, user=anna, signature_html="<p>Anna</p>")

    text, html = parts(message_from(anna.pk))

    assert html.startswith("<p>Following up.</p><p>Anna</p><p>Legal.</p>")
    assert html.endswith(f'<a href="{escape(link())}" style="{LINK_STYLE}">{PL_LABEL}</a></p>')
    assert text == f"Following up.\n\nAnna\n\n-- \nLegal.\n\n{PL_LABEL}: {link()}"


# --- footers per brand ---


def test_footer_lookup_language_and_brand_cascade(channel):
    pl, en = LanguageFactory(iso2="PL"), LanguageFactory(iso2="EN")
    assert channel.default_language_id == pl.pk
    save = footer_service.save_footer
    save(channel, pl, "<div>PL</div>{{ legal }}")
    assert footer_service.resolve(channel, "en", "ecom.house").html.startswith("<div>PL</div>")
    save(channel, pl, "<div>PL ecom</div>{{ legal }}", "ecom.house")
    assert footer_service.resolve(channel, "en", "ecom.house").html.startswith("<div>PL ecom</div>")
    save(channel, en, "<div>EN</div>{{ legal }}")
    assert footer_service.resolve(channel, "en", "ecom.house").html.startswith("<div>EN</div>")
    save(channel, en, "<div>EN ecom</div>{{ legal }}", "ecom.house")
    assert footer_service.resolve(channel, "en", "ecom.house").html.startswith("<div>EN ecom</div>")
    assert footer_service.resolve(channel, "en").html.startswith("<div>EN</div>")
    assert footer_service.resolve(channel, "en", "entirius").html.startswith("<div>EN</div>")


def test_mail_takes_the_footer_of_its_mailbox_brand(channel, static_template, policy):
    anna = staff("anna")
    make_mailbox(channel, user=anna, brand="ecom.house")
    pl = LanguageFactory(iso2="PL")
    footer_service.save_footer(channel, pl, "<div>All</div>{{ legal }}")
    footer_service.save_footer(channel, pl, "<div>Ecom</div>{{ legal }}", "ecom.house")

    assert mail_builder.footer_html(message_from(anna.pk)) == "<div>Ecom</div><p>Legal.</p>"
    assert mail_builder.footer_html(message_from(ref="ss:9")) == "<div>All</div><p>Legal.</p>"


def test_footer_api_brand_param_and_backward_compatible_default(channel, admin_api):
    LanguageFactory(iso2="PL")
    plain = admin_api.put(api_url("footers/pl/"), {"html": "<div>All</div>{{ legal }}"}, format="json")
    branded = admin_api.put(
        api_url("footers/pl/?brand=ecom.house"),
        {"html": "<div>Ecom</div>{{ legal }}", "unsubscribe_label": "  Stop   mail "},
        format="json",
    )

    assert (plain.status_code, plain.json()["brand"], plain.json()["unsubscribe_label"]) == (200, "", "")
    assert (branded.json()["brand"], branded.json()["unsubscribe_label"]) == ("ecom.house", "Stop mail")
    assert admin_api.get(api_url("footers/pl/")).json()["html"].startswith("<div>All</div>")
    assert admin_api.get(api_url("footers/pl/?brand=ecom.house")).json()["html"].startswith("<div>Ecom</div>")
    assert admin_api.get(api_url("footers/pl/?brand=Ecom.House")).json()["brand"] == "ecom.house"
    assert admin_api.get(api_url("footers/pl/?brand=entirius")).status_code == 404
    assert admin_api.get(api_url("footers/pl/?brand=Ecom House")).status_code == 400
    listed = admin_api.get(api_url("footers/")).json()["results"]
    assert [(row["language"], row["brand"]) for row in listed] == [("pl", ""), ("pl", "ecom.house")]
    assert admin_api.delete(api_url("footers/pl/?brand=ecom.house")).status_code == 204
    assert list(MailFooter.objects.values_list("brand", flat=True)) == [""]
    too_long = {"html": "{{ legal }}", "unsubscribe_label": "x" * 121}
    assert admin_api.put(api_url("footers/pl/"), too_long, format="json").status_code == 400


# --- the unsubscribe link in the footer ---


def test_placeholder_takes_the_link_with_the_footer_label(channel, static_template, policy, sandbox, unsubscribe_on):
    html = '<div>{{ legal }}</div><p style="font-size:11px">{{unsubscribe}} · <a href="https://x.test">Site</a></p>'
    footer = footer_service.save_footer(channel, LanguageFactory(iso2="PL"), html, unsubscribe_label="Stop")
    assert "{{ unsubscribe }}" in footer.html

    text, built = parts(message_from())

    anchor = f'<a href="{escape(link())}" style="{LINK_STYLE}">Stop</a>'
    assert built.endswith(
        f'<p style="font-size:11px">{anchor} · <a href="https://x.test" rel="noopener noreferrer">Site</a></p>'
    )
    assert built.count(escape(link())) == 1 and PARAGRAPH_STYLE not in built
    assert text.endswith(f"Legal.\n\nStop <{link()}> · Site <https://x.test>")
    message = Message.objects.get()
    send_service.run_send_due()
    message.refresh_from_db()
    assert anchor in message.footer_html  # the footer as sent


def test_without_base_url_the_placeholder_goes_and_nothing_is_appended(channel, static_template, policy):
    footer_service.save_footer(channel, LanguageFactory(iso2="PL"), "<div>{{ legal }}</div><p>{{ unsubscribe }}</p>")

    text, html = parts(message_from())

    assert html.endswith("<div><p>Legal.</p></div><p></p>")
    assert "unsubscribe" not in html and "unsubscribe" not in text


def test_footer_without_placeholder_gets_the_styled_link_below(channel, static_template, policy, unsubscribe_on):
    footer_service.save_footer(channel, LanguageFactory(iso2="PL"), "<div>{{ legal }}</div>")

    html = parts(message_from())[1]

    assert html.endswith(
        f'<div><p>Legal.</p></div><p style="{PARAGRAPH_STYLE}"><a href="{escape(link())}" '
        f'style="{LINK_STYLE}">{PL_LABEL}</a></p>'
    )


@pytest.mark.parametrize(
    ("html", "error"),
    [
        ("{{ legal }}<p>{{ unsubscribe }}</p><p>{{unsubscribe}}</p>", "at most once"),
        ('{{ legal }}<img src="https://x.test/l.png" alt="{{ unsubscribe }}">', "not inside an attribute"),
    ],
)
def test_unsubscribe_placeholder_twice_or_in_an_attribute_is_400(channel, admin_api, html, error):
    LanguageFactory(iso2="PL")

    response = admin_api.put(api_url("footers/pl/"), {"html": html}, format="json")

    assert response.status_code == 400
    details = {detail["field"]: detail["description"] for detail in response.json()["details"]}
    assert error in details["html"]


def test_our_quoted_footer_link_line_is_not_an_optout():
    thread = SimpleNamespace(recipient_language=SimpleNamespace(iso2="PL"))
    body = f"Dziękuję, proszę o telefon.\n\nFrom: Outreach\nCompany\n{PL_LABEL} <{BASE}{unsubscribe_service.PATH_PREFIX}abc/>"

    assert not inbound_service.has_optout_phrase(thread, body)


# --- migrations 0027 / 0028 ---


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def before_0027(transactional_db):
    apps = _migrate([(APP, "0026_message_referral_request")])
    yield apps
    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes(APP))


def test_migration_moves_cc_address_and_makes_user_mailboxes_their_default(before_0027):
    channel = before_0027.get_model(APP, "Channel").objects.create(idx="default-europe", label="Default")
    old = before_0027.get_model(APP, "Mailbox")
    user = get_user_model().objects.create_user(username="anna", is_staff=True)
    old.objects.create(channel=channel, address="a@mail.test", cc_address="CRM@mail.test", user_id=user.pk)
    old.objects.create(channel=channel, address="team@mail.test")

    new = _migrate([(APP, "0028_sender_settings_constraints")]).get_model(APP, "Mailbox")

    rows = {row.address: row for row in new.objects.all()}
    assert (rows["a@mail.test"].cc_addresses, rows["a@mail.test"].is_user_default) == (["crm@mail.test"], True)
    assert (rows["team@mail.test"].cc_addresses, rows["team@mail.test"].is_user_default) == ([], False)
    assert rows["a@mail.test"].bcc_addresses == [] and rows["a@mail.test"].brand == ""

    back = _migrate([(APP, "0026_message_referral_request")]).get_model(APP, "Mailbox")
    assert back.objects.get(address="a@mail.test").cc_address == "crm@mail.test"
