# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""HTML mail footer: placeholder rule, sanitising, language cascade, text part, sent snapshot, GDPR."""

import pytest
from django.core import mail

from django_communicator import gdpr
from django_communicator.enums import MessageStatus
from django_communicator.models import MailFooter, Message
from django_communicator.schemas.requests import RecipientData
from django_communicator.services import footer_service, mail_builder, send_service
from django_communicator.services.communicate_service import communicate
from tests.conftest import CHANNEL_IDX, api_url, approved_message
from tests.factories import LanguageFactory

LEGAL = "Data controller: Example Seller.\n\nYou can object at any time."
FOOTER = (
    '<table><tr><td><img src="https://cdn.example.test/logo.png" alt="Logo"></td>'
    "<td><strong>Jan Kowalski</strong><br>Example sp. z o.o.<br>"
    '<a href="https://example.test">Our site</a> · <a href="mailto:biuro@example.test">biuro@example.test</a>'
    "</td></tr></table><div>{{ legal }}</div>"
)


def _save(channel, iso2: str = "PL", html: str = FOOTER) -> MailFooter:
    return footer_service.save_footer(channel, LanguageFactory(iso2=iso2), html)


def _parts(message: Message) -> tuple[str, str]:
    built = mail_builder.build(message).message()
    return tuple(part.get_payload(decode=True).decode().rstrip("\n") for part in built.get_payload())


def test_put_get_list_delete_footer(channel, admin_api):
    put = admin_api.put(api_url("footers/pl/"), {"html": FOOTER}, format="json")

    assert put.status_code == 200 and put.json()["language"] == "pl"
    assert admin_api.get(api_url("footers/pl/")).json()["html"] == put.json()["html"]
    assert [row["language"] for row in admin_api.get(api_url("footers/")).json()["results"]] == ["pl"]
    assert admin_api.delete(api_url("footers/pl/")).status_code == 204
    assert admin_api.get(api_url("footers/pl/")).status_code == 404
    assert not MailFooter.objects.exists()


@pytest.mark.parametrize("html", ["<p>Jan Kowalski</p>", "<p>{{ legal }}</p><p>{{legal}}</p>"])
def test_put_without_single_placeholder_is_400_on_html(channel, admin_api, html):
    response = admin_api.put(api_url("footers/pl/"), {"html": html}, format="json")

    assert response.status_code == 400
    assert "html" in {detail["field"] for detail in response.json()["details"]}
    assert not MailFooter.objects.exists()


@pytest.mark.parametrize(
    "html", ['<a href="{{ legal }}">Privacy</a>', '<img src="https://x.test/l.png" alt="{{legal}}">']
)
def test_placeholder_inside_an_attribute_is_400_on_html(channel, admin_api, html):
    response = admin_api.put(api_url("footers/pl/"), {"html": html}, format="json")

    assert response.status_code == 400
    details = {detail["field"]: detail["description"] for detail in response.json()["details"]}
    assert "not inside an attribute" in details["html"]
    assert not MailFooter.objects.exists()


def test_placeholder_as_link_text_is_accepted(channel):
    html = _save(channel, html='<a href="https://example.test/privacy">{{ legal }}</a>').html

    assert html == '<a href="https://example.test/privacy" rel="noopener noreferrer">{{ legal }}</a>'


def test_placeholder_hidden_in_a_stripped_tag_does_not_count(channel):
    with pytest.raises(footer_service.FooterError):
        _save(channel, html="<script>{{ legal }}</script><p>Jan</p>")


def test_unknown_language_is_404_and_customer_is_403(channel, admin_api, customer_api):
    assert admin_api.put(api_url("footers/xx/"), {"html": FOOTER}, format="json").status_code == 404
    assert customer_api.get(api_url("footers/")).status_code == 403


def test_footer_is_sanitised_on_save(channel):
    dirty = (
        '<div style="color: #333; position: fixed; padding: 0 4px" onclick="steal()">{{legal}}</div>'
        '<script>alert(1)</script><a href="javascript:alert(1)">x</a><iframe src="https://evil.test"></iframe>'
        '<img src="https://cdn.example.test/l.png" onerror="x()"><font color="red">Kept text</font>'
    )

    html = _save(channel, html=dirty).html

    assert html.startswith('<div style="color:#333;padding:0 4px">{{ legal }}</div>')
    for gone in ("onclick", "position", "script", "alert", "javascript", "iframe", "onerror", "<font"):
        assert gone not in html
    assert '<img src="https://cdn.example.test/l.png">' in html and "Kept text" in html


def test_style_values_loading_or_evaluating_are_dropped(channel):
    dirty = (
        '<div style="background-color: URL(https://evil.test/t.png); color: #333; '
        'width: expression(alert(1)); height: u\\72l(x); padding: 4px">{{ legal }}</div>'
    )

    assert _save(channel, html=dirty).html == '<div style="color:#333;padding:4px">{{ legal }}</div>'


def test_text_part_links_as_text_url_images_dropped():
    html = footer_service.render(MailFooter(html=FOOTER), LEGAL)

    assert footer_service.to_text(html) == (
        "Jan Kowalski\nExample sp. z o.o.\nOur site <https://example.test> · biuro@example.test\n\n"
        "Data controller: Example Seller.\n\nYou can object at any time."
    )


def test_mail_carries_footer_html_and_text(policy, sandbox, static_template):
    _save(policy.channel)
    message = approved_message(footer=LEGAL)

    text, html = _parts(message)

    assert html.startswith("<p>Following up.</p><table>")
    assert html.endswith("<div><p>Data controller: Example Seller.</p><p>You can object at any time.</p></div>")
    assert text.startswith("Following up.\n\n-- \nJan Kowalski\n")
    assert text.endswith("Data controller: Example Seller.\n\nYou can object at any time.")


def test_cascade_recipient_language_then_channel_default_then_none(policy, sandbox, static_template):
    message = approved_message(footer=LEGAL)  # recipient language pl = channel default
    assert mail_builder.footer_html(message) == ""
    assert _parts(message)[1].endswith("<p>Data controller: Example Seller.\n\nYou can object at any time.</p>")

    _save(policy.channel, "PL", "<div>PL</div>{{ legal }}")
    en = Message.objects.get(pk=message.pk)
    en.thread.recipient_language = LanguageFactory(iso2="EN")
    assert mail_builder.footer_html(en).startswith("<div>PL</div>")

    _save(policy.channel, "EN", "<div>EN</div>{{ legal }}")
    assert mail_builder.footer_html(en).startswith("<div>PL</div>")  # the body is the PL template
    en.template_version = None  # no template version: the recipient language decides
    assert mail_builder.footer_html(en).startswith("<div>EN</div>")


def test_footer_follows_the_body_language_not_the_recipient_language(policy, sandbox, static_template):
    _save(policy.channel, "PL", "<div>PL</div>{{ legal }}")
    _save(policy.channel, "EN", "<div>EN</div>{{ legal }}")
    recipient = RecipientData(email="john@example-shop-1.test", first_name="John", language="en", legal_footer="L.")

    message = communicate(  # no EN template: the body falls back to the channel default PL
        channel_idx=CHANNEL_IDX,
        template_key="followup",
        recipient=recipient,
        context={},
        subject_ref="lang:1",
        requires_review=False,
    )

    assert message.thread.recipient_language.iso2 == "EN"
    assert mail_builder.footer_html(message) == "<div>PL</div><p>L.</p>"


def test_footer_without_legal_text_renders_without_placeholder(policy, sandbox, static_template):
    _save(policy.channel, html="<div>Jan</div>{{ legal }}")

    assert mail_builder.footer_html(approved_message()) == "<div>Jan</div>"


def test_sent_message_keeps_the_footer_it_went_out_with(policy, sandbox, static_template):
    _save(policy.channel, html="<div>Old</div>{{ legal }}")
    message = approved_message(footer="Legal.")

    send_service.run_send_due()
    _save(policy.channel, html="<div>New</div>{{ legal }}")

    message.refresh_from_db()
    assert message.status == MessageStatus.SENT
    assert message.footer_html == "<div>Old</div><p>Legal.</p>"
    assert "<div>Old</div>" in mail.outbox[0].alternatives[0][0]


def test_gdpr_export_and_erase_cover_footer_html(policy, sandbox, static_template):
    _save(policy.channel)
    approved_message(email="jan@example-shop-1.test", footer="Legal.")
    send_service.run_send_due()

    exported = gdpr.gdpr_export("jan@example-shop-1.test")
    gdpr.gdpr_erase("jan@example-shop-1.test")

    assert "<p>Legal.</p>" in exported["Message"][0]["footer_html"]
    assert Message.objects.get().footer_html == ""
