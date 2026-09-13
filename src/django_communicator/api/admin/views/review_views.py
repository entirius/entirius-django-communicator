# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — review queue: list, next, accept, rewrite, edit, skip, skip company."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminPagination, AdminView, Conflict, parse
from django_communicator.models import Message
from django_communicator.schemas.requests import EditRequest, ReviewListQuery, RewriteRequest, SkipRequest
from django_communicator.schemas.responses import MessageDetailResponse, MessageListResponse
from django_communicator.services import review_service
from django_communicator.services.message_service import InvalidTransitionError

_TAGS = ["Communicator review"]
_ACTION_ERRORS = {**ERROR_RESPONSES, 409: None}


def _payload(message: Message) -> dict:
    return MessageDetailResponse.of(message).model_dump(mode="json")


class ReviewListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_review_list",
        summary="List messages of the channel by status",
        description="Oldest first, paginated (`page`, `page_size` ≤ 100). Default status `review_required`.",
        parameters=[
            OpenApiParameter("status", str, description="Message status."),
            OpenApiParameter("page", int, description="Page number."),
        ],
        responses={200: MessageListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(ReviewListQuery, request.query_params.dict())
        qs = review_service.list_messages(self.channel(channel_idx), status=query.status)
        paginator = AdminPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        payload = MessageListResponse(
            count=paginator.page.paginator.count,
            next=paginator.get_next_link(),
            previous=paginator.get_previous_link(),
            results=[MessageDetailResponse.of(message) for message in page],
        )
        return Response(payload.model_dump(mode="json"))


class ReviewNextView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="Oldest message waiting for review",
        description="With thread, template version and the caller's context; 404 when the queue is empty.",
        responses={200: MessageDetailResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        message = review_service.next_for_review(self.channel(channel_idx))
        if message is None:
            raise NotFound("No message waits for review.")
        return Response(_payload(message))


class _MessageActionView(AdminView):
    def message(self, channel_idx: str, pk: int) -> Message:
        try:
            return review_service.get_message(self.channel(channel_idx), pk)
        except Message.DoesNotExist:
            raise NotFound("Message not found.") from None

    def act(self, action, *args, status: int = 200, **kwargs) -> Response:
        """Run a review action; illegal transitions answer 409."""
        try:
            return Response(_payload(action(*args, **kwargs)), status=status)
        except (InvalidTransitionError, review_service.ReviewError) as error:
            raise Conflict(str(error)) from None


class AcceptView(_MessageActionView):
    @extend_schema(
        tags=_TAGS, summary="Accept a message", request=None, responses={200: MessageDetailResponse, **_ACTION_ERRORS}
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        return self.act(review_service.accept, self.message(channel_idx, pk), user=request.user)


class RewriteView(_MessageActionView):
    @extend_schema(
        tags=_TAGS,
        summary="Rewrite an AI draft with reviewer notes",
        description="Creates the next version through the toolbox; the reviewed one becomes `superseded`. "
        "A toolbox failure returns a `failed` version and keeps the reviewed message in the queue.",
        request=RewriteRequest,
        responses={201: MessageDetailResponse, **_ACTION_ERRORS},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(RewriteRequest, request.data)
        return self.act(review_service.rewrite, self.message(channel_idx, pk), notes=body.notes, status=201)


class EditView(_MessageActionView):
    @extend_schema(
        tags=_TAGS,
        summary="Edit a message by hand",
        description="Creates the next version with `edited_by_human`; no toolbox call.",
        request=EditRequest,
        responses={201: MessageDetailResponse, **_ACTION_ERRORS},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(EditRequest, request.data)
        message = self.message(channel_idx, pk)
        return self.act(review_service.edit, message, subject=body.subject, body_text=body.body_text, status=201)


class SkipView(_MessageActionView):
    @extend_schema(
        tags=_TAGS,
        summary="Reject a message",
        request=SkipRequest,
        responses={200: MessageDetailResponse, **_ACTION_ERRORS},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(SkipRequest, request.data or {})
        return self.act(review_service.skip, self.message(channel_idx, pk), user=request.user, reason=body.reason)


class SkipCompanyView(_MessageActionView):
    @extend_schema(
        tags=_TAGS,
        summary="Reject a message and skip its subject",
        description="Emits `company_skipped(subject_ref)`; the owner of the reference decides what that means.",
        request=SkipRequest,
        responses={200: MessageDetailResponse, **_ACTION_ERRORS},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(SkipRequest, request.data or {})
        message = self.message(channel_idx, pk)
        return self.act(review_service.skip_company, message, user=request.user, reason=body.reason)
