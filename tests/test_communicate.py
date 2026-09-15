# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import json

import httpx
import pytest
from django_notifications.models import Notification
from django_utils.toolbox.testing import error_response

from django_communicator.enums import FailureCode, MessageStatus
from django_communicator.models import Message, Thread
from django_communicator.services import suppression_service
from django_communicator.services.communicate_service import (
    LegalFooterRequiredError,
    RecipientData,
    ThreadMismatchError,
    communicate,
)
from tests.conftest import CHANNEL_IDX, DRAFT, draft_response
from tests.factories import ChannelFactory, LanguageFactory, make_static, make_template


def _communicate(recipient, context, key="lead.cold.shop", **kwargs) -> Message:
    return communicate(
        channel_idx=CHANNEL_IDX, template_key=key, recipient=recipient, context=context, subject_ref="bdd:42", **kwargs
    )


@pytest.fixture
def notifications_channel(db):
    from django_notifications.models import Channel as NotificationsChannel

    return NotificationsChannel.objects.create(idx=CHANNEL_IDX, label="Notifications")


def test_C01_ai_template_creates_review_required_draft(ai_template, toolbox, recipient, context):
    message = _communicate(recipient, context)

    assert message.status == MessageStatus.REVIEW_REQUIRED
    assert message.subject == DRAFT["subject"]
    assert message.body_text == "Hello Jan,\n\nYour shop loads slowly."
    assert message.template_version == ai_template.current_version
    assert "Write to Jan at Example Shop." in message.rendered_prompt
    assert (message.model, message.attempts, message.render_context) == ("fake-chat", 1, context)
    assert message.usage == {"input_tokens": 12, "output_tokens": 5, "cost": "0.000022"}
    sent = json.loads(toolbox["complete"].calls.last.request.content)
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert sent["tags"] == ["communicator.draft", f"channel:{CHANNEL_IDX}"]
    assert sent["json_schema"] == ai_template.json_schema


def test_C02_static_template_auto_approves_without_toolbox(static_template, toolbox, recipient, context):
    approved = _communicate(recipient, context, key="followup", requires_review=False)
    reviewed = _communicate(recipient, context, key="followup")

    assert (approved.status, approved.subject, approved.body_text) == (
        MessageStatus.APPROVED,
        "Hi Jan",
        "Following up.",
    )
    assert reviewed.status == MessageStatus.REVIEW_REQUIRED
    assert toolbox["complete"].call_count == 0
    assert approved.thread_id == reviewed.thread_id


def test_C03_legal_footer_required_refuses(channel, toolbox, recipient, context):
    make_template(channel, requires_legal_footer=True)

    with pytest.raises(LegalFooterRequiredError):
        _communicate(recipient, context)

    assert not Message.objects.exists() and not Thread.objects.exists()
    assert toolbox["complete"].call_count == 0
    footer = recipient.model_copy(update={"legal_footer": "Controller: Example Ltd."})
    assert _communicate(footer, context).legal_footer == "Controller: Example Ltd."


def test_C04_missing_placeholder_fails_render(ai_template, toolbox, recipient):
    message = _communicate(recipient, {"unused": "x"})

    assert (message.status, message.failure_code) == (MessageStatus.FAILED, FailureCode.RENDER)
    assert message.failure_detail == "missing placeholders: company_name"
    assert toolbox["complete"].call_count == 0


def test_C05_language_fallback_then_no_template(ai_template, toolbox, context):
    german = RecipientData(email="hans@example-shop-2.test", first_name="Hans", language="de")
    LanguageFactory(iso2="DE")

    fallback = _communicate(german, context)
    missing = _communicate(german, context, key="lead.cold.unknown")

    assert fallback.template_version == ai_template.current_version
    assert fallback.thread.recipient_language.iso2 == "DE"
    assert (missing.status, missing.failure_code) == (MessageStatus.FAILED, FailureCode.NO_TEMPLATE)
    assert toolbox["complete"].call_count == 1


def test_C05_no_fallback_without_channel_default(channel, toolbox, recipient, context):
    make_template(channel, language=LanguageFactory(iso2="EN"))

    message = _communicate(recipient, context)

    assert message.failure_code == FailureCode.NO_TEMPLATE


@pytest.mark.parametrize(
    ("kind", "value", "email"),
    [
        ("email", "Blocked@Example-Shop-9.test", "blocked@example-shop-9.test"),
        ("domain", "www.example-blocked.test", "a@shop.example-blocked.test"),
    ],
)
def test_C06_suppressed_recipient_no_toolbox_call(ai_template, toolbox, context, kind, value, email):
    suppression_service.create_suppression(ai_template.channel, kind=kind, value=value)

    message = _communicate(RecipientData(email=email, language="pl"), context)

    assert message.status == MessageStatus.SUPPRESSED
    assert toolbox["complete"].call_count == 0


def test_C06_domain_suppression_is_not_a_substring_match(ai_template, toolbox, context):
    suppression_service.create_suppression(ai_template.channel, kind="domain", value="shop.pl")

    message = _communicate(RecipientData(email="jan@myshop.pl", first_name="Jan", language="pl"), context)

    assert message.status == MessageStatus.REVIEW_REQUIRED


def test_C10_consumer_budget_failed_no_retry_notifies(ai_template, toolbox, recipient, context, notifications_channel):
    toolbox["complete"].mock(return_value=error_response(402, "BUDGET_EXCEEDED"))

    message = _communicate(recipient, context)

    assert (message.status, message.failure_code, message.attempts) == (MessageStatus.FAILED, FailureCode.BUDGET, 1)
    assert toolbox["complete"].call_count == 1
    notification = Notification.objects.get()
    assert (notification.severity, notification.recipient_role, notification.subject_ref) == (
        "high",
        "sales_admin",
        "bdd:42",
    )


def test_C33_consumer_model_not_allowed_failed_model_notifies(
    ai_template, toolbox, recipient, context, notifications_channel
):
    toolbox["complete"].mock(return_value=error_response(403, "MODEL_NOT_ALLOWED"))

    message = _communicate(recipient, context)

    assert (message.status, message.failure_code) == (MessageStatus.FAILED, FailureCode.MODEL)
    assert toolbox["complete"].call_count == 1
    assert Notification.objects.get().severity == "high"


def test_C11_consumer_schema_invalid_failed_no_prompt_in_detail(
    ai_template, toolbox, recipient, context, notifications_channel
):
    field_errors = {"body_paragraphs": ["required"]}
    toolbox["complete"].mock(return_value=error_response(422, "SCHEMA_INVALID", "Write to Jan", field_errors))

    message = _communicate(recipient, context)

    assert (message.status, message.failure_code) == (MessageStatus.FAILED, FailureCode.SCHEMA)
    assert "SCHEMA_INVALID" in message.failure_detail and "body_paragraphs" in message.failure_detail
    assert "Write to" not in message.failure_detail and not message.rendered_prompt
    assert Notification.objects.get().severity == "medium"


def test_C11_consumer_unusable_output_is_schema_failure(ai_template, toolbox, recipient, context):
    toolbox["complete"].mock(return_value=draft_response({"ok": True}))

    assert _communicate(recipient, context).failure_code == FailureCode.SCHEMA


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(504, json={"error": "UPSTREAM_TIMEOUT"}),
        httpx.Response(503),
        httpx.Response(500),
        httpx.ConnectError("down"),
    ],
)
def test_C12_consumer_upstream_single_attempt_failed(ai_template, toolbox, recipient, context, answer):
    route = toolbox["complete"]
    route.mock(side_effect=answer) if isinstance(answer, Exception) else route.mock(return_value=answer)

    message = _communicate(recipient, context)

    assert (message.status, message.failure_code) == (MessageStatus.FAILED, FailureCode.UPSTREAM)
    assert route.call_count == 1


def test_thread_is_reused_for_the_same_subject_and_recipient(static_template, recipient, context):
    first = _communicate(recipient, context, key="followup")
    upper = recipient.model_copy(update={"email": recipient.email.upper()})

    assert _communicate(upper, context, key="followup").thread_id == first.thread_id
    assert Thread.objects.count() == 1


def test_failure_without_notifications_channel_still_returns_failed(ai_template, toolbox, recipient, context):
    toolbox["complete"].mock(return_value=error_response(402, "BUDGET_EXCEEDED"))

    assert _communicate(recipient, context).failure_code == FailureCode.BUDGET


def test_inactive_template_does_not_resolve(channel, recipient, context):
    make_static(channel, is_active=False)

    assert _communicate(recipient, context, key="followup").failure_code == FailureCode.NO_TEMPLATE


@pytest.mark.parametrize("subject", ["x" * 256, "Line one\nLine two", "Line one\rLine two"])
def test_C11_overlong_or_multiline_subject_fails_as_schema(ai_template, toolbox, recipient, context, subject):
    toolbox["complete"].mock(return_value=draft_response({**DRAFT, "subject": subject}))

    message = _communicate(recipient, context)

    assert (message.status, message.failure_code) == (MessageStatus.FAILED, FailureCode.SCHEMA)


def test_thread_from_other_channel_rejected(static_template, toolbox, recipient, context):
    other = ChannelFactory(idx="other-channel", default_language=static_template.channel.default_language)
    foreign = Thread.objects.create(channel=other, subject_ref="bdd:42", recipient_email=recipient.email)
    own = _communicate(recipient, context, key="followup").thread
    other_recipient = recipient.model_copy(update={"email": "anna@example-shop-2.test"})

    with pytest.raises(ThreadMismatchError):
        _communicate(recipient, context, key="followup", thread=foreign)
    with pytest.raises(ThreadMismatchError):
        _communicate(other_recipient, context, key="followup", thread=own)
    assert Message.objects.filter(thread=foreign).count() == 0 and Message.objects.count() == 1


def test_context_cannot_override_recipient_fields(static_template, recipient):
    message = _communicate(recipient, {"first_name": "Mallory", "email": "x@evil.test"}, key="followup")

    assert message.subject == "Hi Jan"


def test_suppression_domain_rejects_email_shape(channel):
    with pytest.raises(ValueError):
        suppression_service.create_suppression(channel, kind="domain", value="jan@shop.pl")
    assert suppression_service.normalise_value("domain", "WWW.Shop.pl") == "shop.pl"


@pytest.mark.parametrize("value", ["foo@", "@shop.pl", "jan@shop", "jan@shop.", "a@b@shop.pl"])
def test_suppression_email_rejects_incomplete(channel, value):
    with pytest.raises(ValueError):
        suppression_service.create_suppression(channel, kind="email", value=value)
    assert suppression_service.normalise_value("email", " Jan@Shop.pl ") == "jan@shop.pl"
