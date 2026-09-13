# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import httpx
import pytest
from django.contrib.auth import get_user_model
from django_utils.toolbox.testing import CANNED_COMPLETION, mock_toolbox
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from django_communicator.services.communicate_service import RecipientData
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
