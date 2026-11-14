# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 — the mailboxes of a channel: CRUD, login test, pause and resume. Passwords are write-only."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_communicator.models import Mailbox
from django_communicator.schemas.requests import MailboxCreateRequest, MailboxPauseRequest, MailboxUpdateRequest
from django_communicator.schemas.responses import MailboxListResponse, MailboxResponse, MailboxTestResponse
from django_communicator.services import clock_service, health_service, mailbox_service

_TAGS = ["Communicator Mailboxes"]


def _body(mailbox: Mailbox) -> dict:
    return MailboxResponse.of(mailbox, mailbox_service.state(mailbox)).model_dump(mode="json")


class _MailboxView(AdminView):
    access_area = "communicator.settings"

    def mailbox(self, channel_idx: str, pk: int) -> Mailbox:
        try:
            return mailbox_service.get_mailbox(self.channel(channel_idx), pk)
        except Mailbox.DoesNotExist:
            raise NotFound("Mailbox not found.") from None


class MailboxListView(_MailboxView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_mailboxes_list",
        summary="Mailboxes of the channel",
        responses={200: MailboxListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        mailboxes = mailbox_service.list_mailboxes(self.channel(channel_idx))
        return Response({"results": [_body(mailbox) for mailbox in mailboxes]})

    @extend_schema(
        tags=_TAGS,
        summary="Add a mailbox",
        description="Passwords are stored encrypted and never returned. A new default takes the flag from the old "
        "one. A taken address or user, an unknown user or a warm-up without `daily_cap` answers 400.",
        request=MailboxCreateRequest,
        responses={201: MailboxResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(MailboxCreateRequest, request.data)
        try:
            mailbox = mailbox_service.create_mailbox(self.channel(channel_idx), body.changes())
        except mailbox_service.MailboxError as error:
            raise ValidationError({"mailbox": [error.client_message]}) from None
        return Response(_body(mailbox), status=201)


class MailboxDetailView(_MailboxView):
    @extend_schema(tags=_TAGS, summary="One mailbox", responses={200: MailboxResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(_body(self.mailbox(channel_idx, pk)))

    @extend_schema(
        tags=_TAGS,
        summary="Change a mailbox",
        description="Only the fields sent change; an omitted or null password keeps the stored one. A new IMAP "
        "host, user or folder restarts the poll cursor; a new SMTP password clears `needs_login`.",
        request=MailboxUpdateRequest,
        responses={200: MailboxResponse, **ERROR_RESPONSES},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(MailboxUpdateRequest, request.data)
        try:
            mailbox = mailbox_service.update_mailbox(self.mailbox(channel_idx, pk), body.changes())
        except mailbox_service.MailboxError as error:
            raise ValidationError({"mailbox": [error.client_message]}) from None
        return Response(_body(mailbox))

    @extend_schema(
        tags=_TAGS,
        summary="Delete a mailbox",
        description="409 while it is the default or a thread sends from it (disable it instead).",
        responses={204: None, 409: None, **ERROR_RESPONSES},
    )
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        try:
            mailbox_service.delete_mailbox(self.mailbox(channel_idx, pk))
        except mailbox_service.MailboxInUseError as error:
            raise Conflict(error.client_message, code="mailbox_in_use") from None
        return Response(status=204)


class MailboxTestView(_MailboxView):
    @extend_schema(
        tags=_TAGS,
        summary="Log in to SMTP and IMAP",
        description="Nothing is sent or read. A working SMTP login clears `needs_login`.",
        request=None,
        responses={200: MailboxTestResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        result = mailbox_service.check_login(self.mailbox(channel_idx, pk))
        return Response(MailboxTestResponse(**result).model_dump())


class MailboxPauseView(_MailboxView):
    @extend_schema(
        tags=_TAGS,
        summary="Pause a mailbox",
        description="Its mail waits; the other mailboxes keep sending. Logged with the user. 409 when already paused.",
        request=MailboxPauseRequest,
        responses={200: MailboxResponse, 409: None, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(MailboxPauseRequest, request.data)
        mailbox = self.mailbox(channel_idx, pk)
        now = clock_service.now_for(mailbox.channel)
        try:
            health_service.pause_manually(mailbox, now, reason=body.reason, user=request.user)
        except health_service.MailboxStateError as error:
            raise Conflict(error.client_message, code="mailbox_state") from None
        return Response(_body(mailbox))


class MailboxResumeView(_MailboxView):
    @extend_schema(
        tags=_TAGS,
        summary="Resume a paused mailbox",
        description="The only way back from a pause, automatic or manual; the health policy then counts newer events "
        "only. Logged with the user. 409 when not paused.",
        request=MailboxPauseRequest,
        responses={200: MailboxResponse, 409: None, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(MailboxPauseRequest, request.data)
        mailbox = self.mailbox(channel_idx, pk)
        try:
            health_service.resume(
                mailbox, clock_service.now_for(mailbox.channel), reason=body.reason, user=request.user
            )
        except health_service.MailboxStateError as error:
            raise Conflict(error.client_message, code="mailbox_state") from None
        return Response(_body(mailbox))
