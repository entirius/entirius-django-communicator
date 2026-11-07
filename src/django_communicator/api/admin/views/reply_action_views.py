# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 — what the module does with a reply of each intent class (T4)."""

from dataclasses import asdict

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_communicator.enums import ReplyIntent
from django_communicator.schemas.requests import ReplyActionRequest
from django_communicator.schemas.responses import ReplyActionListResponse, ReplyActionResponse
from django_communicator.services import reply_action_service
from django_communicator.services.reply_action_service import Plan

_TAGS = ["Communicator Reply Actions"]


def _row(plan: Plan) -> dict:
    return ReplyActionResponse.model_validate(asdict(plan)).model_dump(mode="json")


def _intent(value: str) -> str:
    if value not in ReplyIntent.values:
        raise NotFound("Unknown intent class.")
    return value


class ReplyActionListView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_reply_actions_list",
        summary="Next step per reply intent class (stored mapping or the safe default)",
        responses={200: ReplyActionListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        rows = [_row(plan) for plan in reply_action_service.effective(self.channel(channel_idx))]
        return Response({"results": rows})


class ReplyActionDetailView(AdminView):
    access_area = "communicator.settings"

    @extend_schema(
        tags=_TAGS,
        summary="Set the next step for one intent class",
        description="Replaces the whole mapping of the class. `pause_until_return` only for `out_of_office`, "
        "`auto_suppress` only for `unsubscribe`. Applies to replies that arrive afterwards.",
        request=ReplyActionRequest,
        responses={200: ReplyActionResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str, intent: str) -> Response:
        body = parse(ReplyActionRequest, request.data)
        channel, intent = self.channel(channel_idx), _intent(intent)
        try:
            plan = reply_action_service.upsert(channel, intent, body.model_dump())
        except reply_action_service.ReplyActionError as error:
            raise ValidationError({"detail": [error.client_message]}) from None
        return Response(_row(plan))

    @extend_schema(
        tags=_TAGS,
        summary="Back to the default for one intent class",
        request=None,
        responses={200: ReplyActionResponse, **ERROR_RESPONSES},
    )
    def delete(self, request: Request, channel_idx: str, intent: str) -> Response:
        channel, intent = self.channel(channel_idx), _intent(intent)
        reply_action_service.reset(channel, intent)
        return Response(_row(reply_action_service.plan_for(channel, intent)))
