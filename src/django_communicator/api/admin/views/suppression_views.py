# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — suppression list (list, add, remove) and why an address may not be mailed."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_communicator.enums import SuppressionScope, SuppressionSource
from django_communicator.models import Suppression
from django_communicator.schemas.requests import AddressStatusQuery, SuppressionListQuery, SuppressionRequest
from django_communicator.schemas.responses import (
    AddressExplainResponse,
    SuppressionListResponse,
    SuppressionResponse,
)
from django_communicator.services import suppression_service

_TAGS = ["Communicator Suppressions"]
AREA = "communicator.settings"


class SuppressionListView(AdminView):
    access_area = AREA

    @extend_schema(
        tags=_TAGS,
        summary="List suppressions of the channel",
        operation_id="communicator_suppressions_list",
        description="The channel's rows and the global rows (`scope: install`: every channel, including the "
        "`email_token` rows of erased or anonymised addresses — no plain address).",
        parameters=[
            OpenApiParameter("value", str, description="Only rows with this value (lower-cased)."),
            OpenApiParameter("scope", str, enum=SuppressionScope.values, description="channel or install (global)."),
            OpenApiParameter("source", str, enum=SuppressionSource.values, description="What wrote the row."),
            OpenApiParameter("search", str, description="Part of the value, case-insensitive."),
        ],
        responses={200: SuppressionListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(SuppressionListQuery, request.query_params.dict())
        filters = {key: str(value or "") for key, value in query.model_dump(mode="json").items()}
        rows = suppression_service.list_suppressions(self.channel(channel_idx), filters)
        results = [SuppressionResponse.of(row) for row in rows]
        return Response(SuppressionListResponse(results=results).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Suppress an email or a registrable domain",
        description="Emails are lower-cased; a domain or host is stored as its registrable domain. The row is a "
        "channel row, or a global one (every channel) when the install runs `COMMUNICATOR_SUPPRESSION_SCOPE = "
        '"install"`; source `manual`.',
        request=SuppressionRequest,
        responses={201: SuppressionResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(SuppressionRequest, request.data)
        channel = self.channel(channel_idx)
        try:
            row = suppression_service.create_suppression(channel, **body.model_dump(mode="json"), user=request.user)
        except suppression_service.InvalidSuppressionValueError as error:
            raise ValidationError({"value": [error.client_message]}) from None
        except suppression_service.DuplicateSuppressionError as error:
            raise Conflict(error.client_message, code="duplicate_suppression") from None
        return Response(SuppressionResponse.of(row).model_dump(mode="json"), status=201)


class SuppressionDetailView(AdminView):
    access_area = AREA

    @extend_schema(
        tags=_TAGS,
        summary="Remove a suppression",
        description="A row of this channel or a global email / domain row (it stops holding in every channel); an "
        "`email_token` row of an erased address cannot be removed (404).",
        responses={204: None, **ERROR_RESPONSES},
    )
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        try:
            suppression_service.delete_suppression(self.channel(channel_idx), pk)
        except Suppression.DoesNotExist:
            raise NotFound("Suppression not found.") from None
        return Response(status=204)


class AddressStatusView(AdminView):
    access_area = AREA

    @extend_schema(
        tags=_TAGS,
        summary="Why an address may (not) be mailed in this channel",
        description="`refusal` is what `communicate()` and the delivery would answer (`address_invalid`, "
        "`suppressed`, null), with the matching suppression rows and the address status.",
        parameters=[OpenApiParameter("email", str, required=True, description="The address to explain.")],
        responses={200: AddressExplainResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        query = parse(AddressStatusQuery, request.query_params.dict())
        explained = suppression_service.explain(self.channel(channel_idx), query.email)
        return Response(AddressExplainResponse.model_validate(explained, from_attributes=True).model_dump(mode="json"))
