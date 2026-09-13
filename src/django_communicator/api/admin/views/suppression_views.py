# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — suppression list: list, add, remove."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_communicator.models import Suppression
from django_communicator.schemas.requests import SuppressionRequest
from django_communicator.schemas.responses import SuppressionListResponse, SuppressionResponse
from django_communicator.services import suppression_service

_TAGS = ["Communicator suppressions"]


class SuppressionListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_suppressions_list",
        summary="List suppressions of the channel",
        responses={200: SuppressionListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        rows = suppression_service.list_suppressions(self.channel(channel_idx))
        results = [SuppressionResponse.model_validate(row) for row in rows]
        return Response(SuppressionListResponse(results=results).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Suppress an email or a registrable domain",
        description="Emails are lower-cased; a domain or host is stored as its registrable domain.",
        request=SuppressionRequest,
        responses={201: SuppressionResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(SuppressionRequest, request.data)
        channel = self.channel(channel_idx)
        try:
            row = suppression_service.create_suppression(channel, **body.model_dump(mode="json"), user=request.user)
        except ValueError as error:
            raise ValidationError({"value": [str(error)]}) from None
        except suppression_service.DuplicateSuppressionError as error:
            raise Conflict(str(error)) from None
        return Response(SuppressionResponse.model_validate(row).model_dump(mode="json"), status=201)


class SuppressionDetailView(AdminView):
    @extend_schema(tags=_TAGS, summary="Remove a suppression", responses={204: None, **ERROR_RESPONSES})
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        try:
            suppression_service.delete_suppression(self.channel(channel_idx), pk)
        except Suppression.DoesNotExist:
            raise NotFound("Suppression not found.") from None
        return Response(status=204)
