# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Outbound Q4: the approval inbox across channels, filters, bulk approve / reject, limit and log."""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import MessageStatus
from django_communicator.models import BulkApprovalLog, Message
from django_communicator.services import sender_route_service
from django_communicator.services.communicate_service import RecipientData, communicate
from tests.conftest import CHANNEL_IDX
from tests.factories import ChannelFactory, make_static

APPROVALS = "/api/communicator/v2/admin/approvals/"


def _draft(email: str, ref: str, *, channel_idx: str = CHANNEL_IDX, route: str = "", key: str = "review.me", **ctx):
    recipient = RecipientData(email=email, first_name="Jan", language="pl")
    return communicate(
        channel_idx=channel_idx,
        template_key=key,
        recipient=recipient,
        context=ctx,
        subject_ref=ref,
        sender_route=route,
    )


@pytest.fixture
def review_template(channel):
    return make_static(channel, key="review.me")


@pytest.fixture
def two_routes(channel):
    sender_route_service.create_route(
        channel, key="ecomhouse", from_email="wiktorw@ecom.house", cc_email="wiktorw@entirius.com"
    )
    sender_route_service.create_route(channel, key="entirius", from_email="wiktorw@entirius.com")


def test_Q4_list_shows_every_channel_with_route_company_template_and_age(admin_api, review_template, two_routes):
    other = ChannelFactory(idx="second", label="Second")
    make_static(other, key="review.me")
    old = _draft("a@example-shop-1.test", "leads.Company:1", route="ecomhouse", company_name="Shop One")
    Message.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=30))
    _draft("b@example-shop-2.test", "leads.Company:2", channel_idx="second")

    body = admin_api.get(APPROVALS).json()

    assert body["count"] == 2 and body["counts"]["by_channel"] == {CHANNEL_IDX: 1, "second": 1}
    first = body["results"][0]  # oldest first
    assert first["id"] == old.pk and first["company"] == "Shop One" and first["template_key"] == "review.me"
    assert (first["sender_route"], first["sender_from"], first["sender_cc"]) == (
        "ecomhouse",
        "wiktorw@ecom.house",
        "wiktorw@entirius.com",
    )
    assert 29 < first["age_hours"] < 31 and first["blocker"] == ""


def test_Q4_filters_by_channel_route_template_search_and_age(admin_api, channel, review_template, two_routes):
    sender_route_service.set_default(channel, "entirius")
    make_static(channel, key="other.tpl")
    a = _draft("a@example-shop-1.test", "leads.Company:1", route="ecomhouse")
    b = _draft("b@example-shop-2.test", "leads.Company:2")  # effective route = the channel default
    c = _draft("c@example-shop-3.test", "leads.Company:3", key="other.tpl")
    Message.objects.filter(pk=a.pk).update(created_at=timezone.now() - timedelta(hours=5))

    def ids(query: str) -> set[int]:
        return {row["id"] for row in admin_api.get(f"{APPROVALS}?{query}").json()["results"]}

    assert ids("sender_route=ecomhouse") == {a.pk}
    assert ids("sender_route=entirius") == {b.pk, c.pk}
    assert ids("template=other.tpl") == {c.pk}
    assert ids("q=shop-2") == {b.pk}
    assert ids("min_age_hours=2") == {a.pk}
    assert ids(f"channel={CHANNEL_IDX}") == {a.pk, b.pk, c.pk} and ids("channel=nope") == set()


def test_Q4_only_drafts_waiting_for_review_are_listed(admin_api, review_template):
    waiting = _draft("a@example-shop-1.test", "x:1")
    done = _draft("b@example-shop-2.test", "x:2")
    Message.objects.filter(pk=done.pk).update(status=MessageStatus.APPROVED)

    assert [row["id"] for row in admin_api.get(APPROVALS).json()["results"]] == [waiting.pk]


def test_Q4_list_query_count_does_not_grow_with_the_page(
    admin_api, review_template, two_routes, django_assert_max_num_queries
):
    for number in range(12):
        _draft(f"p{number}@example-shop-1.test", f"x:{number}", route="ecomhouse")

    with django_assert_max_num_queries(14):
        assert admin_api.get(APPROVALS).json()["count"] == 12


def test_Q4_bulk_approve_logs_who_and_reports_each_failure(admin_api, review_template):
    good = [_draft(f"g{n}@example-shop-1.test", f"x:{n}") for n in range(2)]
    gone = _draft("z@example-shop-9.test", "x:9")
    Message.objects.filter(pk=gone.pk).update(status=MessageStatus.REJECTED)

    response = admin_api.post(
        APPROVALS + "bulk/", {"action": "approve", "ids": [m.pk for m in good] + [gone.pk, 999999]}, format="json"
    )

    body = response.json()
    assert response.status_code == 200 and body["done"] == [m.pk for m in good]
    assert set(body["failed"]) == {str(gone.pk), "999999"}
    approved = Message.objects.filter(pk__in=body["done"])
    assert {m.status for m in approved} == {MessageStatus.APPROVED}
    assert {m.reviewed_by_id for m in approved} == {admin_api.user.pk}
    log = BulkApprovalLog.objects.get(pk=body["id"])
    assert (log.user_id, log.action, log.done_ids) == (admin_api.user.pk, "approve", body["done"])
    listed = admin_api.get(APPROVALS + "log/").json()
    assert listed["results"][0]["user_id"] == admin_api.user.pk and listed["results"][0]["requested"] == 4


def test_Q4_bulk_reject_stores_the_reason(admin_api, review_template):
    draft = _draft("a@example-shop-1.test", "x:1")

    admin_api.post(APPROVALS + "bulk/", {"action": "reject", "ids": [draft.pk], "reason": "bad fit"}, format="json")

    draft.refresh_from_db()
    assert (draft.status, draft.reject_reason, draft.reviewed_by_id) == (
        MessageStatus.REJECTED,
        "bad fit",
        admin_api.user.pk,
    )


def test_Q4_bulk_refuses_more_ids_than_the_limit(admin_api, review_template):
    drafts = [_draft(f"l{n}@example-shop-1.test", f"x:{n}") for n in range(3)]

    with mock.patch.object(communicator_settings, "COMMUNICATOR_BULK_APPROVAL_MAX", 2):
        response = admin_api.post(
            APPROVALS + "bulk/", {"action": "approve", "ids": [m.pk for m in drafts]}, format="json"
        )

    assert response.status_code == 400
    assert Message.objects.filter(status=MessageStatus.REVIEW_REQUIRED).count() == 3
    assert BulkApprovalLog.objects.count() == 0


def test_Q4_bulk_skips_a_draft_with_an_unusable_route_and_says_why(admin_api, review_template):
    bad = _draft("a@example-shop-1.test", "x:1", route="ghost")
    good = _draft("b@example-shop-2.test", "x:2")

    rows = {row["id"]: row for row in admin_api.get(APPROVALS).json()["results"]}
    body = admin_api.post(APPROVALS + "bulk/", {"action": "approve", "ids": [bad.pk, good.pk]}, format="json").json()

    assert "ghost" in rows[bad.pk]["blocker"] and rows[good.pk]["blocker"] == ""
    assert body["done"] == [good.pk] and "ghost" in body["failed"][str(bad.pk)]


def test_Q4_endpoints_are_staff_only_and_validate(customer_api, admin_api):
    assert customer_api.get(APPROVALS).status_code == 403
    assert customer_api.post(APPROVALS + "bulk/", {"action": "approve", "ids": [1]}, format="json").status_code == 403
    assert admin_api.post(APPROVALS + "bulk/", {"action": "delete", "ids": [1]}, format="json").status_code == 400
    assert admin_api.post(APPROVALS + "bulk/", {"action": "approve", "ids": []}, format="json").status_code == 400
