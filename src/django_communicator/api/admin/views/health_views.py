# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 — sending health of a channel and its mailboxes, the auto-pause policy and the pause log."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminPagination, AdminView, parse
from django_communicator.schemas.requests import HealthPolicyRequest
from django_communicator.schemas.responses import (
    HealthPolicyResponse,
    HealthReportResponse,
    PauseLogListResponse,
    PauseLogResponse,
)
from django_communicator.services import clock_service, health_service

_TAGS = ["Communicator Health"]


class HealthView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_TAGS,
        summary="Sending health of the channel and each mailbox",
        description="Counts and percent rates for the last 24 h and 7 d; per mailbox also its state and today's use "
        "of its cap (`sent_today` / `cap_today`, the warm-up included).",
        responses={200: HealthReportResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        channel = self.channel(channel_idx)
        report = health_service.report(channel, clock_service.now_for(channel))
        return Response(HealthReportResponse.of(report).model_dump(mode="json", by_alias=True))


class HealthPolicyView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_TAGS,
        summary="Auto-pause policy of the channel",
        description="Off by default. Judged at the start of every send run of a live channel.",
        responses={200: HealthPolicyResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        policy = health_service.get_policy(self.channel(channel_idx))
        return Response(HealthPolicyResponse.model_validate(policy).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Replace the auto-pause policy",
        description="A mailbox that sent at least `min_sample` mails within `window_hours` and is above a limit is "
        "paused; only a person resumes it.",
        request=HealthPolicyRequest,
        responses={200: HealthPolicyResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str) -> Response:
        body = parse(HealthPolicyRequest, request.data)
        policy = health_service.save_policy(self.channel(channel_idx), body.model_dump())
        return Response(HealthPolicyResponse.model_validate(policy).model_dump(mode="json"))


class HealthLogView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_health_log_list",
        summary="Pauses and resumes of the channel's mailboxes",
        description="Newest first, paginated: automatic pauses carry the measured rates, manual ones the user.",
        parameters=[OpenApiParameter("page", int), OpenApiParameter("page_size", int)],
        responses={200: PauseLogListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        paginator = AdminPagination()
        page = paginator.paginate_queryset(health_service.list_logs(self.channel(channel_idx)), request, view=self)
        return paginator.get_paginated_response(
            [PauseLogResponse.model_validate(log).model_dump(mode="json") for log in page]
        )
