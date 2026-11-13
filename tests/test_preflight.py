# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Preflight: the legal footer is checked at approval and again at delivery."""

from django.core import mail

from django_communicator.enums import FailureCode, MessageStatus
from django_communicator.models import Message, MessageTemplate
from django_communicator.services import preflight_service, send_service
from django_communicator.services.communicate_service import RecipientData, communicate
from tests.conftest import CHANNEL_IDX, api_url, approved_message
from tests.factories import make_static


def _review_required(channel, footer: str = "") -> Message:
    make_static(channel, key="cold", requires_legal_footer=False)
    recipient = RecipientData(email="jan@example-shop-1.test", first_name="Jan", legal_footer=footer)
    return communicate(channel_idx=CHANNEL_IDX, template_key="cold", recipient=recipient, context={}, subject_ref="x:1")


def _require_footer(message: Message) -> None:
    MessageTemplate.objects.filter(versions__pk=message.template_version_id).update(requires_legal_footer=True)


def test_message_with_footer_or_template_without_requirement_passes(channel):
    message = _review_required(channel)

    assert preflight_service.problem(message) is None


def test_template_requiring_a_footer_cannot_be_approved_without_one(channel, admin_api):
    message = _review_required(channel)
    _require_footer(message)

    response = admin_api.post(api_url(f"review/{message.pk}/accept/"))

    message.refresh_from_db()
    assert response.status_code == 409 and response.json()["error"] == "REVIEW_REFUSED"
    assert message.status == MessageStatus.REVIEW_REQUIRED


def test_template_less_message_without_a_footer_cannot_be_approved(channel, admin_api):
    message = _review_required(channel)
    Message.objects.filter(pk=message.pk).update(template_version=None)

    response = admin_api.post(api_url(f"review/{message.pk}/accept/"))

    assert response.status_code == 409


def test_template_less_message_with_a_footer_can_be_approved(channel, admin_api):
    message = _review_required(channel, footer="Seller.")
    Message.objects.filter(pk=message.pk).update(template_version=None)

    assert admin_api.post(api_url(f"review/{message.pk}/accept/")).status_code == 200


def test_approved_message_whose_template_now_requires_a_footer_does_not_leave(policy, sandbox, static_template):
    message = approved_message()
    _require_footer(message)

    send_service.run_send_due()

    message.refresh_from_db()
    assert (message.status, message.failure_code) == (MessageStatus.FAILED, FailureCode.LEGAL_FOOTER)
    assert mail.outbox == []


def test_approved_template_less_message_without_a_footer_does_not_leave(policy, sandbox, static_template):
    message = approved_message()
    Message.objects.filter(pk=message.pk).update(template_version=None)

    send_service.run_send_due()

    message.refresh_from_db()
    assert (message.status, message.failure_code) == (MessageStatus.FAILED, FailureCode.LEGAL_FOOTER)
    assert mail.outbox == []
