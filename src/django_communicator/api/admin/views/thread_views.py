# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 — threads: list by subject reference, one thread with its timeline."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminPagination, AdminView, parse
from django_communicator.models import Thread
from django_communicator.schemas.requests import ThreadListQuery
from django_communicator.schemas.responses import ThreadDetailResponse, ThreadListResponse, ThreadSummaryResponse
from django_communicator.services import inbox_service

_TAGS = ["Communicator inbox"]


class ThreadListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_threads_list",
        summary="Threads of the channel, newest first",
        parameters=[
            OpenApiParameter("subject_ref", str, description="Only threads about this reference."),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: ThreadListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(ThreadListQuery, request.query_params.dict())
        threads = inbox_service.list_threads(self.channel(channel_idx), subject_ref=query.subject_ref)
        paginator = AdminPagination()
        page = paginator.paginate_queryset(threads, request, view=self)
        results = [ThreadSummaryResponse.model_validate(thread).model_dump(mode="json") for thread in page]
        return paginator.get_paginated_response(results)


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
        body = ThreadDetailResponse(**summary, timeline=inbox_service.timeline(thread))
        return Response(body.model_dump(mode="json"))
