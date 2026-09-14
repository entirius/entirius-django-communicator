# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 — replies: list by kind/thread, confirm or dismiss a suspected opt-out."""

from collections.abc import Callable

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminPagination, AdminView, Conflict, parse
from django_communicator.enums import ReplyKind
from django_communicator.models import Reply
from django_communicator.schemas.requests import ReplyListQuery
from django_communicator.schemas.responses import ReplyListResponse, ReplyResponse
from django_communicator.services import inbox_service, optout_service

_TAGS = ["Communicator inbox"]


class ReplyListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_replies_list",
        summary="Replies of the channel, newest first",
        parameters=[
            OpenApiParameter("kind", str, enum=ReplyKind.values, description="Only replies of this kind."),
            OpenApiParameter("thread", int, description="Only replies of this thread."),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: ReplyListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(ReplyListQuery, request.query_params.dict())
        channel = self.channel(channel_idx)
        replies = inbox_service.list_replies(channel, kind=query.kind, thread_id=query.thread)
        paginator = AdminPagination()
        page = paginator.paginate_queryset(replies, request, view=self)
        return paginator.get_paginated_response([_reply(reply) for reply in page])


def _reply(reply: Reply) -> dict:
    return ReplyResponse.model_validate(reply).model_dump(mode="json")


def _decide(view: AdminView, channel_idx: str, pk: int, action: Callable[[Reply], Reply]) -> Response:
    try:
        reply = inbox_service.get_reply(view.channel(channel_idx), pk)
        return Response(_reply(action(reply)))
    except Reply.DoesNotExist:
        raise NotFound("Reply not found.") from None
    except optout_service.OptoutStateError as error:
        raise Conflict(str(error)) from None


class ConfirmOptoutView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="Confirm a suspected opt-out",
        description="Suppresses the thread recipient, stops the sequence and emits optout_confirmed; any other reply → 409.",
        request=None,
        responses={200: ReplyResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        return _decide(self, channel_idx, pk, lambda reply: optout_service.confirm(reply, user=request.user))


class DismissOptoutView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="Dismiss a suspected opt-out",
        description="The reply becomes a plain reply (no notification); the sequence stays paused until resume-sequence; any other → 409.",
        request=None,
        responses={200: ReplyResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        return _decide(self, channel_idx, pk, optout_service.dismiss)
