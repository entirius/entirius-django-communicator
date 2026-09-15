# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from datetime import datetime, time
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest
from celery import current_app
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django_utils.toolbox.testing import CANNED_COMPLETION, mock_toolbox
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from django_communicator.enums import ChannelMode
from django_communicator.models import Channel, Message, SendPolicy, SendWindow
from django_communicator.services import channel_service, clock_service, counter_service
from django_communicator.services.communicate_service import RecipientData, communicate
from tests.factories import ChannelFactory, make_static, make_template

CHANNEL_IDX = "default-europe"
DRAFT = {"subject": "A question about your shop", "body_paragraphs": ["Hello Jan,", "Your shop loads slowly."]}


def draft_response(parsed: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json={**CANNED_COMPLETION, "parsed": DRAFT if parsed is None else parsed})


@pytest.fixture
def channel(db):
    return ChannelFactory()


@pytest.fixture
def ai_template(channel):
    return make_template(channel)


@pytest.fixture
def static_template(channel):
    return make_static(channel, auto_approve=True)


@pytest.fixture
def toolbox():
    """respx router over the toolbox; `complete` answers a valid draft."""
    with mock_toolbox() as router:
        router["complete"].mock(return_value=draft_response())
        yield router


@pytest.fixture
def recipient() -> RecipientData:
    return RecipientData(email="jan@example-shop-1.test", first_name="Jan", last_name="Kowalski", language="pl")


@pytest.fixture
def context() -> dict:
    return {"company_name": "Example Shop"}


def _client_for(username: str, **flags) -> APIClient:
    user = get_user_model().objects.create_user(username=username, **flags)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    client.user = user
    return client


@pytest.fixture
def admin_api(db) -> APIClient:
    return _client_for("reviewer", is_staff=True)


@pytest.fixture
def customer_api(db) -> APIClient:
    return _client_for("customer")


def api_url(path: str) -> str:
    return f"/api/communicator/v2/admin/{CHANNEL_IDX}/{path}"


WARSAW = ZoneInfo("Europe/Warsaw")
MONDAY_10 = datetime(2026, 9, 14, 10, 0, tzinfo=WARSAW)


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """The daily counter runs on fakeredis; the cache (clock override, alert dedup) starts empty."""
    server = fakeredis.FakeRedis()
    monkeypatch.setattr(counter_service, "_client", lambda: server)
    cache.clear()
    yield server
    cache.clear()


@pytest.fixture
def once_backend(tmp_path):
    """celery-once on a file backend, as the host app would configure it on Redis."""
    config = {"backend": "celery_once.backends.File", "settings": {"location": str(tmp_path), "default_timeout": 60}}
    current_app.conf.update(ONCE=config)
    yield config
    current_app.conf.update(ONCE=None)


@pytest.fixture
def policy(channel) -> SendPolicy:
    """Business days, 08:00–17:00 Warsaw, cap 10, no spread; the channel clock stands at Monday 10:00."""
    policy = SendPolicy.objects.create(channel=channel, daily_cap=10, spread=False)
    SendWindow.objects.create(policy=policy, start_time=time(8), end_time=time(17))
    clock_service.set_override(channel, MONDAY_10)
    return policy


@pytest.fixture
def sandbox(channel) -> Channel:
    return channel_service.set_mode(channel, mode=ChannelMode.SANDBOX, sandbox_mailbox="sandbox@mail.example.test")


def approved_message(
    email: str = "jan@example-shop-1.test", subject_ref: str = "send:1", footer: str = "", context: dict | None = None
) -> Message:
    """A static auto-approve message (needs the `static_template` fixture)."""
    recipient = RecipientData(email=email, first_name="Jan", last_name="Kowalski", language="pl", legal_footer=footer)
    return communicate(
        channel_idx=CHANNEL_IDX,
        template_key="followup",
        recipient=recipient,
        context=context or {},
        subject_ref=subject_ref,
        requires_review=False,
    )
