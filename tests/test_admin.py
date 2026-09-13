# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest
from django.contrib.auth import get_user_model

from django_communicator.admin import MessageAdmin
from django_communicator.models import Message, MessageTemplate


@pytest.fixture
def superuser_client(client, db):
    client.force_login(get_user_model().objects.create_superuser("root", "root@example.test", "pw"))
    return client


def test_admin_pages_render(superuser_client, ai_template):
    for url in ("channel", "messagetemplate", "suppression", "message"):
        assert superuser_client.get(f"/admin/django_communicator/{url}/").status_code == 200
    assert (
        superuser_client.get(f"/admin/django_communicator/messagetemplate/{ai_template.pk}/change/").status_code == 200
    )


def test_admin_save_creates_a_version(superuser_client, ai_template):
    url = f"/admin/django_communicator/messagetemplate/{ai_template.pk}/change/"
    data = {
        "channel": ai_template.channel_id,
        "key": ai_template.key,
        "kind": ai_template.kind,
        "language": ai_template.language_id,
        "subject": ai_template.subject,
        "body": "New body {first_name}",
        "json_schema": '{"type": "object"}',
        "model": "fake-chat",
        "is_active": "on",
        "versions-TOTAL_FORMS": "1",
        "versions-INITIAL_FORMS": "1",
        "versions-MIN_NUM_FORMS": "0",
        "versions-MAX_NUM_FORMS": "1000",
        "versions-0-id": ai_template.current_version_id,
        "versions-0-template": ai_template.pk,
    }

    response = superuser_client.post(url, data)

    assert response.status_code == 302
    assert MessageTemplate.objects.get().current_version.number == 2


def test_message_admin_is_read_only(rf):
    admin = MessageAdmin(Message, None)

    assert not any(check(rf.get("/")) for check in (admin.has_add_permission, admin.has_delete_permission))
