# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Text pool edit and removal: a used text is deactivated, never deleted; the picker skips inactive texts."""

import random

import pytest

from django_communicator.models import Sequence, TextPool, Thread, ThreadPoolUsage, ThreadSequenceState
from django_communicator.services import sending_config_service, sequence_service
from tests.conftest import api_url
from tests.factories import ChannelFactory


@pytest.fixture
def sequence(channel) -> Sequence:
    return Sequence.objects.create(channel=channel, key="followup")


@pytest.fixture
def text(sequence) -> TextPool:
    return TextPool.objects.create(sequence=sequence, body="TEST follow-up 1")


def _url(text: TextPool) -> str:
    return api_url(f"sequences/{text.sequence_id}/texts/{text.pk}/")


def _use(text: TextPool) -> Thread:
    thread = Thread.objects.create(channel=text.sequence.channel, subject_ref="pool:1", recipient_email="a@b.test")
    ThreadPoolUsage.objects.create(thread=thread, text=text)
    return thread


def test_patch_edits_body_and_restores(text, admin_api):
    TextPool.objects.filter(pk=text.pk).update(is_active=False)

    response = admin_api.patch(_url(text), {"body": "Any news?", "is_active": True}, format="json")

    text.refresh_from_db()
    assert response.status_code == 200
    assert response.json() == {"id": text.pk, "body": "Any news?", "is_active": True}
    assert (text.body, text.is_active) == ("Any news?", True)


@pytest.mark.parametrize("body", [{"body": ""}, {"body": "x" * 4001}, {"sequence": 2}, {"is_active": "maybe"}])
def test_patch_refuses_invalid_body(text, admin_api, body):
    assert admin_api.patch(_url(text), body, format="json").status_code == 400
    text.refresh_from_db()
    assert (text.body, text.is_active) == ("TEST follow-up 1", True)


def test_patch_of_a_text_deleted_meanwhile_is_404(text, admin_api, monkeypatch):
    read = sending_config_service.get_text

    def read_then_deleted(sequence, pk):
        row = read(sequence, pk)
        TextPool.objects.filter(pk=pk).delete()  # a concurrent DELETE between the read and the save
        return row

    monkeypatch.setattr(sending_config_service, "get_text", read_then_deleted)

    assert admin_api.patch(_url(text), {"body": "Any news?"}, format="json").status_code == 404


def test_delete_unused_text_deletes(text, admin_api):
    response = admin_api.delete(_url(text))

    assert response.status_code == 204
    assert not TextPool.objects.filter(pk=text.pk).exists()


def test_delete_used_text_deactivates_and_keeps_history(text, admin_api):
    thread = _use(text)

    response = admin_api.delete(_url(text))

    assert response.status_code == 200
    assert response.json() == {"id": text.pk, "body": "TEST follow-up 1", "is_active": False}
    assert ThreadPoolUsage.objects.filter(thread=thread, text=text).exists()
    assert TextPool.objects.get(pk=text.pk).is_active is False


def test_text_of_another_sequence_or_channel_is_404(channel, text, admin_api):
    sibling = Sequence.objects.create(channel=channel, key="other")
    other_channel = ChannelFactory(idx="other-channel")
    foreign = TextPool.objects.create(sequence=Sequence.objects.create(channel=other_channel, key="followup"), body="x")

    wrong_sequence = api_url(f"sequences/{sibling.pk}/texts/{text.pk}/")
    wrong_channel = api_url(f"sequences/{foreign.sequence_id}/texts/{foreign.pk}/")

    for url in (wrong_sequence, wrong_channel):
        assert admin_api.patch(url, {"is_active": False}, format="json").status_code == 404
        assert admin_api.delete(url).status_code == 404
    assert TextPool.objects.filter(is_active=True).count() == 2


def test_text_detail_needs_staff(text, customer_api):
    assert customer_api.patch(_url(text), {"is_active": False}, format="json").status_code == 403
    assert customer_api.delete(_url(text)).status_code == 403


def test_pick_text_never_picks_inactive(sequence, text):
    inactive = TextPool.objects.create(sequence=sequence, body="TEST retired", is_active=False)
    thread = _use(text)
    state = ThreadSequenceState.objects.create(thread=thread, sequence=sequence)

    picked = {sequence_service.pick_text(state, random.Random(seed)) for seed in range(20)}

    assert picked == {text}
    TextPool.objects.filter(pk=text.pk).update(is_active=False)
    assert sequence_service.pick_text(state, random.Random(0)) is None
    assert inactive.pk not in {t.pk for t in picked}
