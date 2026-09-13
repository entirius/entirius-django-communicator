# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Development-only endpoint that lets BDD call `communicate()` — 404 outside `ENVIRONMENT == "development"`."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, DevelopmentView, parse
from django_communicator.schemas.requests import DevCommunicateRequest
from django_communicator.schemas.responses import MessageDetailResponse
from django_communicator.services.communicate_service import LegalFooterRequiredError, communicate

_TAGS = ["Communicator (development)"]


class DevCommunicateView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Call communicate() (development only)",
        description="Returns the created message; a missing legal footer answers 400 and creates nothing.",
        request=DevCommunicateRequest,
        responses={201: MessageDetailResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DevCommunicateRequest, request.data)
        channel = self.channel(channel_idx)
        try:
            message = communicate(channel_idx=channel.idx, **dict(body))
        except LegalFooterRequiredError as error:
            raise ValidationError({"recipient.legal_footer": [str(error)]}) from None
        return Response(MessageDetailResponse.of(message).model_dump(mode="json"), status=201)
