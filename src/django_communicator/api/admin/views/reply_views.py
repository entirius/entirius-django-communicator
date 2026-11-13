# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 — replies: list by kind/thread/class, confirm or dismiss a suspected opt-out; reply class profile
and plans."""

from collections.abc import Callable

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminPagination, AdminView, Conflict, parse
from django_communicator.enums import ReplyIntent, ReplyKind
from django_communicator.models import Reply, ReplyClassProfile
from django_communicator.schemas.requests import ReplyClassPlanRequest, ReplyClassProfileRequest, ReplyListQuery
from django_communicator.schemas.responses import (
    ReplyClassPlanListResponse,
    ReplyClassPlanResponse,
    ReplyClassProfileResponse,
    ReplyListResponse,
    ReplyResponse,
)
from django_communicator.services import inbox_service, optout_service, reply_class_service, reply_plan_service

_TAGS = ["Communicator Inbox"]


class ReplyListView(AdminView):
    access_area = "communicator.conversations"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_replies_list",
        summary="Replies of the channel, newest first",
        parameters=[
            OpenApiParameter("kind", str, enum=ReplyKind.values, description="Only replies of this kind."),
            OpenApiParameter("thread", int, description="Only replies of this thread."),
            OpenApiParameter("intent", str, enum=ReplyIntent.values, description="Only replies of this class."),
            OpenApiParameter("needs_review", bool, description="Only replies flagged (true) or not (false)."),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: ReplyListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(ReplyListQuery, request.query_params.dict())
        channel = self.channel(channel_idx)
        filters = inbox_service.ReplyFilter(
            kind=query.kind, thread_id=query.thread, intent=query.intent, needs_review=query.needs_review
        )
        replies = inbox_service.list_replies(channel, filters)
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
        raise Conflict(error.client_message, code="optout_state") from None


class ConfirmOptoutView(AdminView):
    access_area = "communicator.conversations"

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
    access_area = "communicator.conversations"

    @extend_schema(
        tags=_TAGS,
        summary="Dismiss a suspected opt-out",
        description="The reply becomes a plain reply (no notification); the sequence stays paused until resume-sequence; any other → 409.",
        request=None,
        responses={200: ReplyResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        return _decide(self, channel_idx, pk, optout_service.dismiss)


_CLASS_TAGS = ["Communicator Reply classes"]


def _profile(profile: ReplyClassProfile) -> dict:
    return ReplyClassProfileResponse.model_validate(profile).model_dump(mode="json")


class ReplyClassProfileView(AdminView):
    access_area = "communicator.content"

    @extend_schema(
        tags=_CLASS_TAGS,
        summary="Reply class profile of the channel",
        description="Prompt and model that classify human replies; 404 while the channel has none (no classes).",
        responses={200: ReplyClassProfileResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        try:
            return Response(_profile(reply_class_service.get_profile(self.channel(channel_idx))))
        except ReplyClassProfile.DoesNotExist:
            raise NotFound("Reply class profile not found.") from None

    @extend_schema(
        tags=_CLASS_TAGS,
        summary="Replace the reply class profile",
        description="Classification runs only with an active profile and the AI toolbox enabled.",
        request=ReplyClassProfileRequest,
        responses={200: ReplyClassProfileResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str) -> Response:
        body = parse(ReplyClassProfileRequest, request.data)
        profile = reply_class_service.save_profile(self.channel(channel_idx), body.model_dump())
        return Response(_profile(profile))


def _plan(plan: reply_plan_service.Plan) -> dict:
    return ReplyClassPlanResponse.model_validate(plan).model_dump(mode="json")


class ReplyClassPlanListView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_CLASS_TAGS,
        summary="What a reply of each class does",
        description="One plan per class: the stored one, else the code default (`is_default`).",
        responses={200: ReplyClassPlanListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        plans = reply_plan_service.effective(self.channel(channel_idx))
        return Response({"results": [_plan(plan) for plan in plans]})


class ReplyClassPlanDetailView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_CLASS_TAGS,
        summary="Replace the plan of one class",
        description="400 for an unknown class, `pause_until_return` outside out_of_office, or an unsubscribe plan "
        "that does not pause (a person always decides an opt-out).",
        request=ReplyClassPlanRequest,
        responses={200: ReplyClassPlanResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str, intent: str) -> Response:
        body = parse(ReplyClassPlanRequest, request.data)
        channel = self.channel(channel_idx)
        try:
            return Response(_plan(reply_plan_service.upsert(channel, intent, body.model_dump())))
        except reply_plan_service.ReplyPlanError as error:
            raise ValidationError({"intent": [error.client_message]}) from None

    @extend_schema(
        tags=_CLASS_TAGS,
        summary="Back to the code default",
        request=None,
        responses={200: ReplyClassPlanResponse, **ERROR_RESPONSES},
    )
    def delete(self, request: Request, channel_idx: str, intent: str) -> Response:
        channel = self.channel(channel_idx)
        try:
            return Response(_plan(reply_plan_service.reset(channel, intent)))
        except reply_plan_service.ReplyPlanError as error:
            raise ValidationError({"intent": [error.client_message]}) from None
