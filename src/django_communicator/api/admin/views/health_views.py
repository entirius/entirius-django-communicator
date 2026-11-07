# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — sending health, auto-pause policy and manual pause / resume of a sender route (outbound Q15)."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_communicator.api.admin.views.sender_route_views import payload as route_payload
from django_communicator.models import HealthPolicy, RoutePauseLog, SenderRoute
from django_communicator.schemas.requests import HealthPolicyBody, RouteReasonBody
from django_communicator.schemas.responses import (
    HealthPolicyResponse,
    HealthResponse,
    PauseLogResponse,
    PauseLogRowResponse,
    SenderRouteResponse,
)
from django_communicator.services import clock_service, health_service

_TAGS = ["Communicator Health"]
_ERRORS = {**ERROR_RESPONSES, 409: None}
LOG_LIMIT = 100


def _policy(policy: HealthPolicy) -> dict:
    return HealthPolicyResponse(
        enabled=policy.enabled,
        window_hours=policy.window_hours,
        min_sample=policy.min_sample,
        max_bounce_pct=float(policy.max_bounce_pct),
        max_optout_pct=float(policy.max_optout_pct),
        max_complaint_pct=float(policy.max_complaint_pct),
    ).model_dump(mode="json")


class HealthView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_health",
        summary="Sending health of the channel and of each sender route",
        description="Counts and percent rates over the last 24 h and 7 days, per route and for the channel. "
        "Rates are percent of mails sent in the same window; null when nothing was sent.",
        responses={200: HealthResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        channel = self.channel(channel_idx)
        body = health_service.report(channel, clock_service.now_for(channel))
        return Response({**body, "policy": _policy(health_service.get_policy(channel))})


class HealthPolicyView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_health_policy",
        summary="Auto-pause thresholds",
        responses={200: HealthPolicyResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        return Response(_policy(health_service.get_policy(self.channel(channel_idx))))

    @extend_schema(
        tags=_TAGS,
        summary="Replace the auto-pause thresholds",
        description="Off by default. When on, a live channel's route is paused as soon as, within `window_hours` and "
        "with at least `min_sample` mails sent, hard bounces, opt-outs or complaints exceed their percent threshold. "
        "A paused route is resumed only by `routes/{key}/resume/`.",
        request=HealthPolicyBody,
        responses={200: HealthPolicyResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str) -> Response:
        body = parse(HealthPolicyBody, request.data)
        return Response(_policy(health_service.save_policy(self.channel(channel_idx), **body.model_dump())))


class PauseLogView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_health_log",
        summary="Pause and resume log",
        responses={200: PauseLogResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        channel = self.channel(channel_idx)
        rows = RoutePauseLog.objects.filter(channel=channel).select_related("user").order_by("-created_at", "-pk")
        results = [
            PauseLogRowResponse(
                id=row.pk,
                route_key=row.route_key,
                action=row.action,
                trigger=row.trigger,
                reason=row.reason,
                metrics=row.metrics,
                user=row.user.get_username() if row.user else None,
                created_at=row.created_at,
            ).model_dump(mode="json")
            for row in rows[:LOG_LIMIT]
        ]
        return Response({"results": results})


class _RouteActionView(AdminView):
    access_area = "communicator.settings"

    def route(self, channel_idx: str, key: str):
        channel = self.channel(channel_idx)
        try:
            return channel, SenderRoute.objects.select_related("channel").get(channel=channel, key=key)
        except SenderRoute.DoesNotExist:
            raise NotFound("Sender route not found.") from None


class RoutePauseView(_RouteActionView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_route_pause",
        summary="Pause a sender route by hand",
        description="No mail leaves by the route (it waits, approved) until `resume/`. 409 when already paused.",
        request=RouteReasonBody,
        responses={200: SenderRouteResponse, **_ERRORS},
    )
    def post(self, request: Request, channel_idx: str, key: str) -> Response:
        body = parse(RouteReasonBody, request.data)
        channel, route = self.route(channel_idx, key)
        try:
            health_service.pause_manually(route, clock_service.now_for(channel), reason=body.reason, user=request.user)
        except health_service.RouteStateError as error:
            raise Conflict(error.client_message, code="route_already_paused") from None
        return Response(route_payload(channel, route))


class RouteResumeView(_RouteActionView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_route_resume",
        summary="Resume a paused sender route",
        description="The only way back from a pause, automatic or manual; a reason is required and logged. The "
        "auto-pause then judges only events after the resume. 409 when the route is not paused.",
        request=RouteReasonBody,
        responses={200: SenderRouteResponse, **_ERRORS},
    )
    def post(self, request: Request, channel_idx: str, key: str) -> Response:
        body = parse(RouteReasonBody, request.data)
        channel, route = self.route(channel_idx, key)
        try:
            route = health_service.resume(route, clock_service.now_for(channel), reason=body.reason, user=request.user)
        except health_service.RouteStateError as error:
            raise Conflict(error.client_message, code="route_not_paused") from None
        return Response(route_payload(channel, route))
