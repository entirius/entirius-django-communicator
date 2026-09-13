# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest
from django_utils.toolbox.testing import error_response

from django_communicator.models import Message, MessageTemplate, Suppression
from django_communicator.services.communicate_service import communicate
from tests.conftest import CHANNEL_IDX, DRAFT, api_url


@pytest.fixture
def draft(ai_template, toolbox, recipient, context) -> Message:
    return communicate(
        channel_idx=CHANNEL_IDX, template_key="lead.cold.shop", recipient=recipient, context=context, subject_ref="r:1"
    )


def test_review_endpoints_need_an_admin(customer_api, channel):
    assert customer_api.get(api_url("review/next/")).status_code == 403


def test_review_next_returns_oldest_with_context(draft, admin_api, django_assert_max_num_queries):
    with django_assert_max_num_queries(6):
        response = admin_api.get(api_url("review/next/"))

    body = response.json()
    assert response.status_code == 200
    assert (body["id"], body["render_context"], body["thread"]["recipient_language"]) == (
        draft.pk,
        draft.render_context,
        "pl",
    )
    assert (body["template"]["key"], body["template"]["version_number"]) == ("lead.cold.shop", 1)


def test_review_next_empty_queue_is_404(channel, admin_api):
    assert admin_api.get(api_url("review/next/")).status_code == 404


def test_review_list_filters_by_status(draft, admin_api):
    assert admin_api.get(api_url("review/")).json()["count"] == 1
    assert admin_api.get(api_url("review/?status=approved")).json()["count"] == 0
    assert admin_api.get(api_url("review/?status=bogus")).status_code == 400


def test_accept_then_accept_again_is_409(draft, admin_api):
    first = admin_api.post(api_url(f"review/{draft.pk}/accept/"))

    assert (first.status_code, first.json()["status"], first.json()["reviewed_by_id"]) == (
        200,
        "approved",
        admin_api.user.pk,
    )
    assert admin_api.post(api_url(f"review/{draft.pk}/accept/")).status_code == 409


def test_rewrite_edit_skip_over_api(draft, admin_api, toolbox):
    rewritten = admin_api.post(api_url(f"review/{draft.pk}/rewrite/"), {"notes": "Shorter."}, format="json").json()
    edited = admin_api.post(
        api_url(f"review/{rewritten['id']}/edit/"), {"subject": "S", "body_text": "B"}, format="json"
    )
    skipped = admin_api.post(api_url(f"review/{edited.json()['id']}/skip-company/"), {"reason": "No"}, format="json")

    assert (rewritten["version"], rewritten["parent_id"], rewritten["subject"]) == (2, draft.pk, DRAFT["subject"])
    assert (edited.status_code, edited.json()["edited_by_human"]) == (201, True)
    assert (skipped.json()["status"], skipped.json()["reject_reason"]) == ("rejected", "No")
    assert admin_api.post(api_url(f"review/{draft.pk}/skip/"), {}, format="json").status_code == 409


def test_message_of_another_channel_is_404(draft, admin_api):
    assert admin_api.post(f"/api/communicator/v2/admin/other/review/{draft.pk}/accept/").status_code == 404


def test_templates_create_replace_versions(channel, admin_api):
    body = {
        "key": "reply.thanks",
        "kind": "static",
        "language": "pl",
        "subject": "Thanks",
        "body": "Thank you, {first_name}.",
    }
    created = admin_api.post(api_url("templates/"), body, format="json")
    pk = created.json()["id"]
    replaced = admin_api.put(api_url(f"templates/{pk}/"), {**body, "body": "Thanks!"}, format="json")
    versions = admin_api.get(api_url(f"templates/{pk}/versions/")).json()["results"]

    assert (created.status_code, created.json()["current_version_number"]) == (201, 1)
    assert (replaced.status_code, replaced.json()["current_version_number"]) == (200, 2)
    assert [v["number"] for v in versions] == [2, 1]
    assert admin_api.get(api_url("templates/")).json()["results"][0]["key"] == "reply.thanks"


@pytest.mark.parametrize(
    "overrides",
    [{"language": "xx"}, {"kind": "ai_prompt", "auto_approve": True, "model": "fake-chat"}, {"key": "Bad Key"}],
)
def test_template_validation_is_400(channel, admin_api, overrides):
    body = {"key": "reply.thanks", "kind": "static", "language": "pl", "body": "Hi", **overrides}

    assert admin_api.post(api_url("templates/"), body, format="json").status_code == 400
    assert not MessageTemplate.objects.exists()


def test_test_generate_does_not_save(ai_template, admin_api, toolbox):
    context = {"first_name": "Jan", "company_name": "Shop"}
    response = admin_api.post(
        api_url(f"templates/{ai_template.pk}/test-generate/"), {"context": context}, format="json"
    )

    assert (response.status_code, response.json()["subject"]) == (200, DRAFT["subject"])
    assert toolbox["complete"].calls.last.request.content.count(b"communicator.test_generate") == 1
    assert not Message.objects.exists()
    missing = admin_api.post(api_url(f"templates/{ai_template.pk}/test-generate/"), {"context": {}}, format="json")
    assert missing.status_code == 400


def test_test_generate_maps_toolbox_errors(ai_template, admin_api, toolbox):
    toolbox["complete"].mock(return_value=error_response(402, "BUDGET_EXCEEDED"))
    context = {"first_name": "Jan", "company_name": "Shop"}

    response = admin_api.post(
        api_url(f"templates/{ai_template.pk}/test-generate/"), {"context": context}, format="json"
    )

    assert (response.status_code, response.json()["error"]) == (402, "BUDGET_EXCEEDED")


def test_models_proxy_the_toolbox_catalogue(channel, admin_api, toolbox):
    results = admin_api.get(api_url("models/")).json()["results"]

    assert results[0]["model_id"] == "fake-chat" and "input_price_per_1k" in results[0]


def test_suppressions_create_duplicate_delete(channel, admin_api):
    created = admin_api.post(api_url("suppressions/"), {"kind": "domain", "value": "WWW.Shop.pl"}, format="json")
    duplicate = admin_api.post(api_url("suppressions/"), {"kind": "domain", "value": "shop.pl"}, format="json")
    invalid = admin_api.post(api_url("suppressions/"), {"kind": "email", "value": "no-at-sign"}, format="json")

    assert (created.status_code, created.json()["value"]) == (201, "shop.pl")
    assert (duplicate.status_code, invalid.status_code) == (409, 400)
    assert admin_api.get(api_url("suppressions/")).json()["results"][0]["value"] == "shop.pl"
    assert admin_api.delete(api_url(f"suppressions/{created.json()['id']}/")).status_code == 204
    assert not Suppression.objects.exists()


def test_dev_communicate_endpoint(ai_template, admin_api, toolbox):
    recipient = {"email": "jan@example-shop-1.test", "first_name": "Jan", "language": "pl"}
    body = {
        "template_key": "lead.cold.shop",
        "recipient": recipient,
        "context": {"company_name": "S"},
        "subject_ref": "r:2",
    }

    response = admin_api.post(api_url("test/communicate/"), body, format="json")

    assert (response.status_code, response.json()["status"]) == (201, "review_required")
    ai_template.requires_legal_footer = True
    ai_template.save()
    assert admin_api.post(api_url("test/communicate/"), body, format="json").status_code == 400
