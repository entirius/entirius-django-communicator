# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 — threads: the inbox lists (threads, conversations; state filter, counts), one thread with its timeline, resume a sequence."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminPagination, AdminView, Conflict, parse
from django_communicator.models import Channel, SendPolicy, Thread
from django_communicator.schemas.requests import ConversationListQuery, ThreadListQuery
from django_communicator.schemas.responses import (
    ConversationCountsResponse,
    ConversationListResponse,
    ConversationRowResponse,
    SequenceStateResponse,
    ThreadCountsResponse,
    ThreadDetailResponse,
    ThreadListResponse,
    ThreadRowResponse,
    ThreadSummaryResponse,
)
from django_communicator.services import inbox_service, optout_service, policy_service

_TAGS = ["Communicator inbox"]


class ThreadListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_threads_list",
        summary="Threads of the channel, newest first",
        parameters=[
            OpenApiParameter("subject_ref", str, description="Only threads about this reference."),
            OpenApiParameter("state", str, enum=list(inbox_service.THREAD_STATES), description="Inbox filter."),
            OpenApiParameter("sort", str, enum=list(inbox_service.THREAD_SORTS), description="Default created."),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: ThreadListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(ThreadListQuery, request.query_params.dict())
        channel = self.channel(channel_idx)
        threads = inbox_service.list_threads(channel, subject_ref=query.subject_ref, state=query.state, sort=query.sort)
        paginator = AdminPagination()
        page = paginator.paginate_queryset(threads, request, view=self)
        rows = inbox_service.thread_rows(page, _policy_or_none(channel))
        response = paginator.get_paginated_response([ThreadRowResponse.of(row).model_dump(mode="json") for row in rows])
        counts = inbox_service.count_states(channel, subject_ref=query.subject_ref)
        response.data["counts"] = ThreadCountsResponse(**counts).model_dump()
        return response


class ConversationListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_conversations_list",
        summary="Conversations of the channel (one row per subject reference), latest activity first",
        parameters=[
            OpenApiParameter("state", str, enum=list(inbox_service.THREAD_STATES), description="Inbox filter."),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: ConversationListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(ConversationListQuery, request.query_params.dict())
        channel = self.channel(channel_idx)
        paginator = AdminPagination()
        page = paginator.paginate_queryset(inbox_service.list_conversations(channel, state=query.state), request, self)
        rows = inbox_service.conversation_rows(page, _policy_or_none(channel))
        results = [ConversationRowResponse.of(row).model_dump(mode="json") for row in rows]
        response = paginator.get_paginated_response(results)
        counts = inbox_service.count_conversations(channel)
        response.data["counts"] = ConversationCountsResponse(**counts).model_dump()
        return response


def _policy_or_none(channel: Channel) -> SendPolicy | None:
    """A bad stored country/timezone must not hide the inbox: the rows then carry no `next_slot`."""
    try:
        return policy_service.load_policy(channel)
    except policy_service.ChannelConfigError:
        return None


class ThreadDetailView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="One thread with its messages and replies",
        responses={200: ThreadDetailResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        try:
            thread = inbox_service.get_thread(channel_idx, pk)
        except Thread.DoesNotExist:
            raise NotFound("Thread not found.") from None
        summary = ThreadSummaryResponse.model_validate(thread).model_dump()
        state = getattr(thread, "sequence_state", None)
        sequence = SequenceStateResponse.model_validate(state) if state else None
        body = ThreadDetailResponse(**summary, sequence=sequence, timeline=inbox_service.timeline(thread))
        return Response(body.model_dump(mode="json"))


class ResumeSequenceView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="Resume the paused sequence of a thread",
        description="After a dismissed opt-out: the thread is open again and the sequence runs, re-armed from the "
        "last delivery. An undecided opt-out or a sequence that is not paused → 409.",
        request=None,
        responses={200: SequenceStateResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        try:
            state = optout_service.resume(inbox_service.get_thread(channel_idx, pk))
        except Thread.DoesNotExist:
            raise NotFound("Thread not found.") from None
        except optout_service.OptoutStateError as error:
            raise Conflict(str(error), code="optout_state") from None
        return Response(SequenceStateResponse.model_validate(state).model_dump(mode="json"))
