# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The approval inbox across channels: list, filters, counts, bulk approve / reject, the log."""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.enums import MessageStatus
from django_communicator.models import BulkApprovalLog, Message, MessageTemplate
from django_communicator.services.communicate_service import RecipientData, communicate
from tests.factories import ChannelFactory, make_static

URL = "/api/communicator/v2/admin/approvals/"
OTHER = "other-europe"


def _draft(channel_idx: str, email: str, footer: str = "Seller.", key: str = "cold") -> Message:
    recipient = RecipientData(email=email, first_name="Jan", legal_footer=footer)
    return communicate(channel_idx=channel_idx, template_key=key, recipient=recipient, context={}, subject_ref=email)


@pytest.fixture
def drafts(channel) -> list[Message]:
    """A draft in each of two channels; the second one's template now requires a footer it does not have."""
    other = ChannelFactory(idx=OTHER, label="Other")
    make_static(channel, key="cold", requires_legal_footer=False)
    make_static(other, key="cold", requires_legal_footer=False)
    first = _draft(channel.idx, "jan@example-shop-1.test")
    second = _draft(OTHER, "ola@example-shop-2.test", footer="")
    MessageTemplate.objects.filter(channel=other).update(requires_legal_footer=True)
    return [first, second]


def test_list_returns_drafts_of_both_channels_with_a_blocker_on_the_one_without_a_footer(drafts, admin_api):
    body = admin_api.get(URL).json()

    rows = {row["id"]: row for row in body["results"]}
    assert [row["id"] for row in body["results"]] == [message.pk for message in drafts]
    assert rows[drafts[0].pk]["blocker"] is None and rows[drafts[0].pk]["channel"] == "default-europe"
    assert "legal footer" in rows[drafts[1].pk]["blocker"] and rows[drafts[1].pk]["channel"] == OTHER
    assert body["counts"] == {"total": 2, "by_channel": {"default-europe": 1, OTHER: 1}, "by_mailbox": {"none": 2}}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({"channel": OTHER}, [1]),
        ({"search": "example-shop-1"}, [0]),
        ({"template_key": "cold"}, [0, 1]),
        ({"template_key": "missing"}, []),
        ({"min_age_hours": 1}, []),
    ],
)
def test_filters(drafts, admin_api, query, expected):
    body = admin_api.get(URL, query).json()

    assert [row["id"] for row in body["results"]] == [drafts[i].pk for i in expected]
    assert body["counts"]["total"] == len(expected)


def test_old_drafts_match_min_age(drafts, admin_api):
    Message.objects.filter(pk=drafts[0].pk).update(created_at=timezone.now() - timedelta(hours=5))

    body = admin_api.get(URL, {"min_age_hours": 4}).json()

    assert [row["id"] for row in body["results"]] == [drafts[0].pk]


def test_bulk_approve_handles_each_id_on_its_own_and_logs(drafts, admin_api):
    ids = [drafts[1].pk, 999999, drafts[0].pk]

    response = admin_api.post(URL + "bulk/", {"action": "approve", "ids": ids}, format="json")

    body = response.json()
    assert response.status_code == 200 and body["done"] == [drafts[0].pk]
    assert set(body["failed"]) == {str(drafts[1].pk), "999999"}
    assert Message.objects.get(pk=drafts[0].pk).status == MessageStatus.APPROVED
    assert Message.objects.get(pk=drafts[1].pk).status == MessageStatus.REVIEW_REQUIRED
    log = BulkApprovalLog.objects.get()
    assert (log.user, log.action, log.ids, log.done) == (admin_api.user, "approve", ids, [drafts[0].pk])


def test_bulk_reject_with_reason(drafts, admin_api):
    ids = [message.pk for message in drafts]

    body = admin_api.post(URL + "bulk/", {"action": "reject", "ids": ids, "reason": "off"}, format="json").json()

    assert body["done"] == ids and body["failed"] == {}
    assert set(Message.objects.filter(pk__in=ids).values_list("status", flat=True)) == {MessageStatus.REJECTED}
    assert BulkApprovalLog.objects.get().reason == "off"


def test_bulk_over_the_limit_is_400_and_changes_nothing(drafts, admin_api):
    with mock.patch.object(communicator_settings, "COMMUNICATOR_BULK_APPROVAL_MAX", 1):
        response = admin_api.post(URL + "bulk/", {"action": "approve", "ids": [d.pk for d in drafts]}, format="json")

    assert response.status_code == 400 and not BulkApprovalLog.objects.exists()
    assert not Message.objects.filter(status=MessageStatus.APPROVED).exists()


def test_bulk_default_limit_is_100(channel, admin_api):
    response = admin_api.post(URL + "bulk/", {"action": "approve", "ids": list(range(1, 102))}, format="json")

    assert response.status_code == 400


@pytest.mark.parametrize("body", [{"action": "send", "ids": [1]}, {"action": "approve", "ids": []}])
def test_bulk_invalid_body_is_400(channel, admin_api, body):
    assert admin_api.post(URL + "bulk/", body, format="json").status_code == 400


def test_log_lists_newest_first(drafts, admin_api):
    admin_api.post(URL + "bulk/", {"action": "approve", "ids": [drafts[0].pk]}, format="json")
    admin_api.post(URL + "bulk/", {"action": "reject", "ids": [drafts[1].pk]}, format="json")

    body = admin_api.get(URL + "log/").json()

    assert [row["action"] for row in body["results"]] == ["reject", "approve"]
    assert body["results"][1]["done"] == [drafts[0].pk] and body["results"][1]["user_id"] == admin_api.user.pk


@pytest.mark.parametrize("path", ["", "bulk/", "log/"])
def test_staff_only(channel, customer_api, path):
    call = customer_api.post if path == "bulk/" else customer_api.get

    assert call(URL + path, {}, format="json").status_code == 403


def test_approvals_route_wins_over_a_channel_named_approvals(drafts, admin_api):
    ChannelFactory(idx="approvals", label="Clash")

    assert admin_api.get(URL).json()["counts"]["total"] == 2


def test_an_unexpected_error_still_logs_the_ids_already_changed(drafts, admin_api):
    from django_communicator.services import approval_service, review_service

    real_accept = review_service.accept

    def accept_then_break(message, *, user):
        if message.pk == drafts[1].pk:
            raise RuntimeError("database gone")
        return real_accept(message, user=user)

    with mock.patch.object(review_service, "accept", side_effect=accept_then_break), pytest.raises(RuntimeError):
        approval_service.bulk(user=admin_api.user, action="approve", ids=[d.pk for d in drafts])

    log = BulkApprovalLog.objects.get()
    assert (log.done, log.failed) == ([drafts[0].pk], {})
