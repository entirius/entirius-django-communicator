# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Shared wiring of the admin views — auth declared explicitly, never inherited from service defaults."""

import uuid
from typing import TypeVar

from django.conf import settings
from django_utils.api.v2_errors import ErrorDetail, ErrorResponse, raise_pydantic_as_drf
from pydantic import BaseModel, ValidationError
from rest_framework.exceptions import APIException, NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_communicator.models import Channel
from django_communicator.services import channel_service
from django_communicator.services.policy_service import ChannelConfigError

SchemaT = TypeVar("SchemaT", bound=BaseModel)

ERROR_RESPONSES = {400: None, 401: None, 403: None, 404: None}


class Conflict(APIException):
    status_code = 409
    default_detail = "The request conflicts with the current state."
    default_code = "conflict"


class AdminPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]

    def handle_exception(self, exc: Exception) -> Response:
        """A stored channel country/timezone the send policy cannot use: 409 `CHANNEL_CONFIG_INVALID`, never 500."""
        if not isinstance(exc, ChannelConfigError):
            return super().handle_exception(exc)
        detail = ErrorDetail(field=None, issue="CHANNEL_CONFIG_INVALID", description=str(exc))
        body = ErrorResponse(
            error="CHANNEL_CONFIG_INVALID",
            message="The channel country or timezone is invalid.",
            debug_id=uuid.uuid4().hex[:8],
            details=[detail],
        )
        return Response(body.model_dump(), status=409)

    @staticmethod
    def channel(channel_idx: str) -> Channel:
        try:
            return channel_service.get_channel(channel_idx)
        except Channel.DoesNotExist:
            raise NotFound("Channel not found.") from None


def parse(schema: type[SchemaT], data: object) -> SchemaT:
    """Validate request data; a Pydantic error becomes the v2 400 shape."""
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise_pydantic_as_drf(exc)


def is_development() -> bool:
    return getattr(settings, "ENVIRONMENT", "") == "development"


class DevelopmentView(AdminView):
    """Answers 404 outside `ENVIRONMENT == "development"`, even when a host mounts the URL."""

    def initial(self, request: Request, *args, **kwargs) -> None:
        if not is_development():
            raise NotFound()
        super().initial(request, *args, **kwargs)
