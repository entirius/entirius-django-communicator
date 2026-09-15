# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 — the channel's IMAP mailbox; the password is write-only."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_communicator.schemas.requests import MailboxRequest
from django_communicator.schemas.responses import MailboxResponse
from django_communicator.services import inbox_service

_TAGS = ["Communicator inbox"]


class MailboxView(AdminView):
    @extend_schema(
        tags=_TAGS, summary="IMAP mailbox of the channel", responses={200: MailboxResponse, **ERROR_RESPONSES}
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        config = inbox_service.get_mailbox(self.channel(channel_idx))
        if config is None:
            raise NotFound("Mailbox not configured.")
        return Response(MailboxResponse.of(config).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Create or replace the IMAP mailbox",
        description="The password is stored encrypted and never returned; omit it to keep the stored one.",
        request=MailboxRequest,
        responses={200: MailboxResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str) -> Response:
        body = parse(MailboxRequest, request.data)
        config = inbox_service.save_mailbox(self.channel(channel_idx), **body.model_dump())
        return Response(MailboxResponse.of(config).model_dump(mode="json"))
