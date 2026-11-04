# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin refusals answer with the text this module wrote (`client_message`), never with `str(exc)`; any other
exception is the v2 500 `INTERNAL_ERROR` with a `debug_id` and nothing of its text."""

from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace

import pytest

from django_communicator.api.admin.views import test_views
from django_communicator.models import Sequence
from django_communicator.services import (
    footer_service,
    inbox_service,
    optout_service,
    preview_service,
    review_service,
    send_service,
    sending_config_service,
    sequence_service,
    suppression_service,
    template_service,
)
from django_communicator.services.communicate_service import LegalFooterRequiredError
from django_communicator.services.exceptions import ClientError
from django_communicator.services.message_service import InvalidTransitionError
from django_communicator.services.policy_service import ChannelConfigError
from django_communicator.services.render_service import RenderError
from tests.conftest import api_url, approved_message

TRAP = "OperationalError: /srv/app/.env token=sk-leak-4f2a9c host=db.internal:5432"
STEP = {"number": 1, "days_after_previous": 3, "template_key": "followup"}
DEV_BODY = {
    "template_key": "followup",
    "recipient": {"email": "jan@example-shop-1.test", "first_name": "Jan", "language": "pl"},
    "subject_ref": "r:1",
}


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    module: ModuleType
    call: str
    body: dict = field(default_factory=dict)
    lookups: tuple[tuple[ModuleType, str], ...] = ()


ACCEPT = Route("post", "review/1/accept/", review_service, "accept", lookups=((review_service, "get_message"),))
REWRITE = Route(
    "post", "review/1/rewrite/", review_service, "rewrite", {"notes": "x"}, ((review_service, "get_message"),)
)
CONFIRM = Route("post", "replies/1/confirm-optout/", optout_service, "confirm", lookups=((inbox_service, "get_reply"),))
RESUME = Route("post", "threads/1/resume-sequence/", optout_service, "resume", lookups=((inbox_service, "get_thread"),))
FOOTER = Route("put", "footers/pl/", footer_service, "save_footer", {"html": "<p>{{ legal }}</p>"})
GENERATE = Route(
    "post",
    "templates/1/test-generate/",
    preview_service,
    "generate_preview",
    lookups=((template_service, "get_template"),),
)
SUPPRESS = Route(
    "post", "suppressions/", suppression_service, "create_suppression", {"kind": "email", "value": "a@shop.test"}
)
SEQUENCE = Route("post", "sequences/", sending_config_service, "create_sequence", {"key": "followup", "steps": [STEP]})
SEND_NOW = Route("post", "messages/1/send-now/", send_service, "send_now", lookups=((review_service, "get_message"),))
DEV_COMMUNICATE = Route("post", "test/communicate/", test_views, "communicate", DEV_BODY)
DEV_SEQUENCE = Route("post", "test/start-sequence/", sequence_service, "start_sequence")


def _refusal(cls: type[ClientError], *args) -> ClientError:
    """A module refusal whose `str()` is the trap: only `client_message` may reach the body."""
    error = cls(*args)
    error.args = (TRAP,)
    return error


def _raising(error: Exception):
    def call(*args, **kwargs):
        raise error

    return call


def _request(admin_api, monkeypatch, route: Route, error: Exception):
    for module, name in route.lookups:
        monkeypatch.setattr(module, name, lambda *args, **kwargs: SimpleNamespace(pk=1))
    monkeypatch.setattr(route.module, route.call, _raising(error))
    return getattr(admin_api, route.method)(api_url(route.path), route.body, format="json")


@pytest.fixture
def dev_sequence_body(static_template) -> dict:
    """A real thread and sequence: the start-sequence view loads both before calling the service."""
    thread = approved_message().thread
    Sequence.objects.create(channel=static_template.channel, key="followup")
    return {"thread_id": thread.pk, "sequence_key": "followup"}


REFUSALS = [
    pytest.param(ACCEPT, InvalidTransitionError, ("message 1 is no longer review_required",), 409, id="review-done"),
    pytest.param(REWRITE, review_service.ReviewError, ("only AI drafts can be rewritten",), 409, id="review-refused"),
    pytest.param(CONFIRM, optout_service.OptoutStateError, ("reply 1 is not an undecided opt-out",), 409, id="reply"),
    pytest.param(RESUME, optout_service.OptoutStateError, ("thread 1 has no paused sequence",), 409, id="resume"),
    pytest.param(FOOTER, footer_service.FooterError, ("The footer must contain it once.",), 400, id="footer"),
    pytest.param(GENERATE, RenderError, (["first_name"],), 400, id="test-generate"),
    pytest.param(
        SUPPRESS, suppression_service.InvalidSuppressionValueError, ("not an email address",), 400, id="suppress-bad"
    ),
    pytest.param(
        SUPPRESS,
        suppression_service.DuplicateSuppressionError,
        ("email a@shop.test is listed",),
        409,
        id="suppress-dup",
    ),
    pytest.param(SEQUENCE, sending_config_service.DuplicateSequenceError, ("sequence followup exists",), 409, id="seq"),
    pytest.param(SEND_NOW, InvalidTransitionError, ("message 1 is not waiting to be sent",), 409, id="send-now"),
    pytest.param(DEV_COMMUNICATE, LegalFooterRequiredError, ("template x requires a footer",), 400, id="dev-footer"),
    pytest.param(ACCEPT, ChannelConfigError, ("Unknown country code.",), 409, id="channel-config"),
]


@pytest.mark.parametrize(("route", "cls", "args", "status"), REFUSALS)
def test_refusal_answers_the_message_the_module_wrote(admin_api, channel, monkeypatch, route, cls, args, status):
    error = _refusal(cls, *args)

    response = _request(admin_api, monkeypatch, route, error)

    assert response.status_code == status
    assert error.client_message in response.content.decode()
    assert "sk-leak" not in response.content.decode()


def test_dev_start_sequence_refusal_answers_its_own_message(admin_api, dev_sequence_body, monkeypatch):
    error = _refusal(sequence_service.SequenceError, "sequence followup cannot run in thread 1")
    route = Route("post", DEV_SEQUENCE.path, DEV_SEQUENCE.module, DEV_SEQUENCE.call, dev_sequence_body)

    response = _request(admin_api, monkeypatch, route, error)

    assert response.status_code == 400
    assert response.json()["details"][0]["description"] == error.client_message
    assert "sk-leak" not in response.content.decode()


def test_resume_without_a_paused_sequence_answers_the_sequence_message(admin_api, static_template):
    """`optout_service.resume` re-raises the `SequenceError` as `OptoutStateError` with the module's own text."""
    thread = approved_message().thread

    response = admin_api.post(api_url(f"threads/{thread.pk}/resume-sequence/"), format="json")

    assert response.status_code == 409
    assert response.json()["message"] == f"thread {thread.pk} has no paused sequence"


def _assert_internal_error(response) -> None:
    body = response.json()
    assert response.status_code == 500
    assert body["error"] == "INTERNAL_ERROR"
    assert body["debug_id"]
    assert body["details"] == []
    assert "sk-leak" not in response.content.decode()
    assert "/srv/app" not in response.content.decode()


ROUTES = [ACCEPT, REWRITE, CONFIRM, RESUME, FOOTER, GENERATE, SUPPRESS, SEQUENCE, SEND_NOW, DEV_COMMUNICATE]


@pytest.mark.parametrize("route", ROUTES, ids=lambda route: route.path)
def test_lower_layer_exception_is_an_internal_error(admin_api, channel, monkeypatch, route):
    _assert_internal_error(_request(admin_api, monkeypatch, route, RuntimeError(TRAP)))


def test_dev_start_sequence_lower_layer_exception_is_an_internal_error(admin_api, dev_sequence_body, monkeypatch):
    route = Route("post", DEV_SEQUENCE.path, DEV_SEQUENCE.module, DEV_SEQUENCE.call, dev_sequence_body)

    _assert_internal_error(_request(admin_api, monkeypatch, route, RuntimeError(TRAP)))


def test_suppression_foreign_value_error_is_an_internal_error(admin_api, channel, monkeypatch):
    """Only the module's own `InvalidSuppressionValueError` is a 400; a `ValueError` from below is not ours."""
    _assert_internal_error(_request(admin_api, monkeypatch, SUPPRESS, ValueError(TRAP)))


@pytest.mark.parametrize("value", ["jan@@shop.test", "no-at-sign"])
def test_unusable_email_is_still_a_400_on_value(admin_api, channel, value):
    response = admin_api.post(api_url("suppressions/"), {"kind": "email", "value": value}, format="json")

    assert response.status_code == 400
    assert response.json()["details"][0]["description"] == "not an email address"


def test_unusable_domain_is_a_400_with_the_module_message(admin_api, channel):
    response = admin_api.post(api_url("suppressions/"), {"kind": "domain", "value": "shop .pl"}, format="json")

    assert response.status_code == 400
    assert response.json()["details"][0]["description"] == "not a domain"
