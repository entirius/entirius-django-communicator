# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import json
from itertools import product

import pytest

from django_communicator import settings as communicator_settings
from django_communicator.enums import MESSAGE_STATUS_TRANSITIONS, MessageStatus
from django_communicator.models import Message
from django_communicator.services import message_service, review_service
from django_communicator.services.communicate_service import communicate
from django_communicator.services.message_service import InvalidTransitionError
from django_communicator.signals import company_skipped, message_approved
from tests.conftest import CHANNEL_IDX
from tests.factories import make_static


def _draft(recipient, context, key="lead.cold.shop", subject_ref="bdd:42") -> Message:
    return communicate(
        channel_idx=CHANNEL_IDX, template_key=key, recipient=recipient, context=context, subject_ref=subject_ref
    )


@pytest.fixture
def draft(ai_template, toolbox, recipient, context) -> Message:
    return _draft(recipient, context)


def test_C07_accept_sets_reviewer(draft, admin_api):
    received = []
    message_approved.connect(lambda sender, message, **kw: received.append(message.pk), weak=False, dispatch_uid="t07")

    accepted = review_service.accept(draft, user=admin_api.user)

    message_approved.disconnect(dispatch_uid="t07")
    assert (accepted.status, accepted.reviewed_by, accepted.scheduled_at) == (
        MessageStatus.APPROVED,
        admin_api.user,
        None,
    )
    assert accepted.reviewed_at is not None and received == [draft.pk]


def test_C08_rewrite_creates_version_and_caps_automated_loop(draft, toolbox):
    human = review_service.rewrite(draft, notes="Mention the checkout.")
    draft.refresh_from_db()

    sent = json.loads(toolbox["complete"].calls.last.request.content)
    assert sent["messages"][-1]["content"].endswith("Mention the checkout.")
    assert sent["tags"] == ["communicator.rewrite", f"channel:{CHANNEL_IDX}"]
    assert (draft.status, human.status, human.parent, human.version) == ("superseded", "review_required", draft, 2)
    assert human.review_notes == "Mention the checkout." and human.automated_rewrites == 0

    current = human
    for _ in range(communicator_settings.COMMUNICATOR_AUTOMATED_REWRITE_LIMIT):
        current = review_service.rewrite(current, notes="Shorter.", automated=True)
    assert (current.version, current.automated_rewrites) == (5, 3)
    with pytest.raises(review_service.RewriteLimitReachedError):
        review_service.rewrite(current, notes="Shorter.", automated=True)
    assert review_service.rewrite(current, notes="Human again.").version == 6


def test_C08_failed_rewrite_keeps_the_reviewed_message(draft, toolbox):
    from django_utils.toolbox.testing import error_response

    toolbox["complete"].mock(return_value=error_response(504, "UPSTREAM_TIMEOUT"))

    failed = review_service.rewrite(draft, notes="Shorter.")
    draft.refresh_from_db()

    assert (failed.status, failed.failure_code, draft.status) == ("failed", "upstream", "review_required")


def test_C08_static_message_cannot_be_rewritten(channel, recipient, context):
    make_static(channel)
    message = _draft(recipient, context, key="followup")

    with pytest.raises(review_service.ReviewError):
        review_service.rewrite(message, notes="x")


def test_C09_manual_edit_no_toolbox_call(draft, toolbox):
    calls = toolbox["complete"].call_count

    edited = review_service.edit(draft, subject="Edited", body_text="By hand.")
    draft.refresh_from_db()

    assert (edited.edited_by_human, edited.version, edited.status, draft.status) == (
        True,
        2,
        "review_required",
        "superseded",
    )
    assert (edited.subject, edited.body_text, edited.template_version) == ("Edited", "By hand.", draft.template_version)
    assert toolbox["complete"].call_count == calls


def test_C29_template_edit_keeps_old_drafts_on_old_version(draft, ai_template, recipient, context):
    from django_communicator.services import template_service

    old_version = ai_template.current_version
    ai_template.body = ai_template.body + " Be brief."
    template_service.save_template(ai_template)
    new_draft = _draft(recipient, context, subject_ref="bdd:43")
    draft.refresh_from_db()

    assert (old_version.number, ai_template.current_version.number) == (1, 2)
    assert draft.template_version == old_version
    assert new_draft.template_version == ai_template.current_version
    assert new_draft.rendered_prompt.endswith("Be brief.")
    template_service.save_template(ai_template)
    assert ai_template.versions.count() == 2


def test_transition_table_every_illegal_edge_raises(draft):
    statuses = [status.value for status in MessageStatus]
    for current, target in product(statuses, statuses):
        Message.objects.filter(pk=draft.pk).update(status=current)
        draft.refresh_from_db()
        if target in MESSAGE_STATUS_TRANSITIONS[current]:
            assert message_service.transition(draft, target).status == target
            continue
        with pytest.raises(InvalidTransitionError):
            message_service.transition(draft, target)


def test_skip_company_rejects_and_emits_subject_ref(draft, admin_api):
    received = []
    company_skipped.connect(
        lambda sender, subject_ref, **kw: received.append(subject_ref), weak=False, dispatch_uid="tsc"
    )

    skipped = review_service.skip_company(draft, user=admin_api.user, reason="Not a fit")

    company_skipped.disconnect(dispatch_uid="tsc")
    assert (skipped.status, skipped.reject_reason, received) == ("rejected", "Not a fit", ["bdd:42"])
