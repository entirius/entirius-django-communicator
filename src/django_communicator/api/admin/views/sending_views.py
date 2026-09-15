# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — sending: channel mode, send policy, outbox with next slots, send now."""

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminPagination, AdminView, Conflict, parse
from django_communicator.enums import ChannelMode, MessageStatus
from django_communicator.models import Message
from django_communicator.schemas.requests import ChannelConfigRequest, OutboxQuery, PolicyRequest
from django_communicator.schemas.responses import (
    ChannelConfigResponse,
    MessageDetailResponse,
    OutboxListResponse,
    OutboxMessageResponse,
    PolicyResponse,
)
from django_communicator.services import (
    channel_service,
    clock_service,
    counter_service,
    policy_service,
    review_service,
    send_service,
    sending_config_service,
)
from django_communicator.services.message_service import InvalidTransitionError

_TAGS = ["Communicator sending"]


def _policy_response(policy) -> dict:
    channel, now = policy.channel, clock_service.now_for(policy.channel)
    windows = sorted(policy.windows.all(), key=lambda window: (window.order, window.start_time))
    return PolicyResponse(
        business_days_only=policy.business_days_only,
        daily_cap=policy.daily_cap,
        spread=policy.spread,
        windows=windows,
        timezone=channel.timezone,
        country=channel.country,
        sent_today=counter_service.sent_on(channel, now.date()),
        next_slot=policy_service.next_slot(policy, now),
    ).model_dump(mode="json")


class ChannelConfigView(AdminView):
    @extend_schema(
        tags=_TAGS, summary="Channel sending mode", responses={200: ChannelConfigResponse, **ERROR_RESPONSES}
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        return Response(ChannelConfigResponse.model_validate(self.channel(channel_idx)).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Change mode or sandbox mailbox",
        description="live and live_enabled are set in Django admin only: `mode=live` or any `live_enabled` answers "
        "400. sandbox without a mailbox answers 409 (C-30).",
        request=ChannelConfigRequest,
        responses={200: ChannelConfigResponse, **ERROR_RESPONSES, 409: None},
    )
    def patch(self, request: Request, channel_idx: str) -> Response:
        body = parse(ChannelConfigRequest, request.data)
        channel = self.channel(channel_idx)
        if body.mode == ChannelMode.LIVE and channel.mode != ChannelMode.LIVE:
            raise ValidationError({"mode": ["live is set in Django admin only"]})
        try:
            channel_service.set_mode(channel, **body.model_dump(mode="json"))
        except DjangoValidationError as error:
            raise Conflict("; ".join(error.messages), code="channel_mode_invalid") from None
        return Response(ChannelConfigResponse.model_validate(channel).model_dump(mode="json"))


class PolicyView(AdminView):
    @extend_schema(
        tags=_TAGS, summary="Send policy of the channel", responses={200: PolicyResponse, **ERROR_RESPONSES, 409: None}
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        policy = policy_service.load_policy(self.channel(channel_idx))
        if policy is None:
            raise NotFound("The channel has no send policy.")
        return Response(_policy_response(policy))

    @extend_schema(
        tags=_TAGS,
        summary="Create or replace the send policy",
        request=PolicyRequest,
        responses={200: PolicyResponse, **ERROR_RESPONSES, 409: None},
    )
    def put(self, request: Request, channel_idx: str) -> Response:
        body = parse(PolicyRequest, request.data)
        channel = self.channel(channel_idx)
        sending_config_service.save_policy(channel, **body.model_dump())
        return Response(_policy_response(policy_service.load_policy(channel)))


class OutboxListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="Outbound messages by status with their next slot",
        parameters=[
            OpenApiParameter("status", str, enum=MessageStatus.values, description="Default approved."),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: OutboxListResponse, **ERROR_RESPONSES, 409: None},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(OutboxQuery, request.query_params.dict())
        channel = self.channel(channel_idx)
        policy = policy_service.load_policy(channel)
        paginator = AdminPagination()
        page = paginator.paginate_queryset(
            review_service.list_messages(channel, status=query.status), request, view=self
        )
        results = [_outbox(message, policy) for message in page]
        return paginator.get_paginated_response(results)


def _outbox(message: Message, policy) -> dict:
    base = MessageDetailResponse.of(message).model_dump()
    slot = send_service.next_slot_for(message, policy)
    return OutboxMessageResponse(**base, next_slot=slot).model_dump(mode="json")


class SendNowView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="Send now",
        description="Sets scheduled_at to the channel clock only; the next beat run still applies mode, policy and cap.",
        request=None,
        responses={200: MessageDetailResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        try:
            message = review_service.get_message(self.channel(channel_idx), pk)
            send_service.send_now(message)
        except Message.DoesNotExist:
            raise NotFound("Message not found.") from None
        except InvalidTransitionError as error:
            raise Conflict(str(error), code="not_waiting") from None
        return Response(MessageDetailResponse.of(message).model_dump(mode="json"))
