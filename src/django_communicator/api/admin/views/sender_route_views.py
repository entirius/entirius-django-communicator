# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — sender routes of a channel (outbound Q3): From mailbox, DW mailbox in CC, mailbox cap, warm-up."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_communicator.models import Channel, MailAccount, SenderRoute
from django_communicator.schemas.requests import SenderRouteBody, SenderRouteCreateRequest
from django_communicator.schemas.responses import SenderRouteListResponse, SenderRouteResponse
from django_communicator.services import clock_service, counter_service, mail_account_service, sender_route_service

_TAGS = ["Communicator Sender Routes"]
_ERRORS = {**ERROR_RESPONSES, 409: None}


def payload(channel: Channel, route: SenderRoute) -> dict:
    today = clock_service.now_for(channel).date()
    return SenderRouteResponse(
        key=route.key,
        name=route.name,
        from_email=route.from_email,
        cc_email=route.cc_email,
        is_active=route.is_active,
        is_default=channel.default_sender_route == route.key,
        daily_cap=route.daily_cap,
        warmup_start_cap=route.warmup_start_cap,
        warmup_step=route.warmup_step,
        warmup_started_on=route.warmup_started_on,
        cap_today=sender_route_service.effective_cap(route, today),
        sent_today=counter_service.mailbox_sent_on(route.from_email, today),
        paused=route.paused_at is not None,
        paused_at=route.paused_at,
        pause_reason=route.pause_reason,
        transport=route.transport,
        mail_account_id=route.mail_account_id,
        mail_account_state=route.mail_account.state if route.mail_account_id else None,
    ).model_dump(mode="json")


def route_fields(channel: Channel, body) -> dict:
    """The service fields of a route request: `mail_account_id` becomes the account of this channel (409 if unknown)."""
    fields = body.model_dump()
    account_id = fields.pop("mail_account_id")
    account = None
    if account_id is not None:
        try:
            account = mail_account_service.get_account(channel, account_id)
        except MailAccount.DoesNotExist:
            raise Conflict("mail account not found in this channel", code="route_config_invalid") from None
    return {**fields, "mail_account": account}


class RouteListView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_routes_list",
        summary="Sender routes of the channel",
        responses={200: SenderRouteListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        channel = self.channel(channel_idx)
        rows = [
            payload(channel, route)
            for route in sender_route_service.list_routes(channel).select_related("mail_account")
        ]
        return Response({"results": rows})

    @extend_schema(
        tags=_TAGS,
        summary="Create a sender route",
        description="Mail of a thread with this route key leaves from `from_email` with `cc_email` in CC. "
        "409 for an existing key or a warm-up without `daily_cap`.",
        request=SenderRouteCreateRequest,
        responses={201: SenderRouteResponse, **_ERRORS},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(SenderRouteCreateRequest, request.data)
        channel = self.channel(channel_idx)
        today = clock_service.now_for(channel).date()
        try:
            route = sender_route_service.create_route(channel, today=today, **route_fields(channel, body))
        except sender_route_service.DuplicateRouteError as error:
            raise Conflict(error.client_message, code="route_exists") from None
        except sender_route_service.RouteConfigError as error:
            raise Conflict(error.client_message, code="route_config_invalid") from None
        return Response(payload(channel, route), status=201)


class RouteDetailView(AdminView):
    access_area = "communicator.settings"

    def route(self, channel: Channel, key: str) -> SenderRoute:
        try:
            return sender_route_service.get_route(channel, key)
        except SenderRoute.DoesNotExist:
            raise NotFound("Sender route not found.") from None

    @extend_schema(tags=_TAGS, summary="One sender route", responses={200: SenderRouteResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, key: str) -> Response:
        channel = self.channel(channel_idx)
        return Response(payload(channel, self.route(channel, key)))

    @extend_schema(
        tags=_TAGS,
        summary="Replace a sender route",
        description="All fields are replaced; the key is fixed. A warm-up without a start date starts today; no "
        "`warmup_start_cap` ends the warm-up.",
        request=SenderRouteBody,
        responses={200: SenderRouteResponse, **_ERRORS},
    )
    def put(self, request: Request, channel_idx: str, key: str) -> Response:
        body = parse(SenderRouteBody, request.data)
        channel = self.channel(channel_idx)
        route = self.route(channel, key)
        today = clock_service.now_for(channel).date()
        try:
            route = sender_route_service.replace_route(route, today=today, **route_fields(channel, body))
        except sender_route_service.RouteConfigError as error:
            raise Conflict(error.client_message, code="route_config_invalid") from None
        return Response(payload(channel, route))

    @extend_schema(
        tags=_TAGS,
        summary="Delete a sender route",
        description="409 while it is the channel default. Threads keep the key; their messages then fail with a "
        "readable error instead of leaving from a wrong mailbox.",
        request=None,
        responses={204: None, **_ERRORS},
    )
    def delete(self, request: Request, channel_idx: str, key: str) -> Response:
        channel = self.channel(channel_idx)
        try:
            sender_route_service.delete_route(self.route(channel, key))
        except sender_route_service.RouteInUseError as error:
            raise Conflict(error.client_message, code="route_is_default") from None
        return Response(status=204)
