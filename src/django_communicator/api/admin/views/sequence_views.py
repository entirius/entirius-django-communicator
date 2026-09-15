# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — follow-up sequences, their steps and text pools."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_communicator.models import Sequence
from django_communicator.schemas.requests import SequenceRequest, TextRequest
from django_communicator.schemas.responses import (
    SequenceListResponse,
    SequenceResponse,
    SequenceStepListResponse,
    SequenceStepResponse,
    TextListResponse,
    TextResponse,
)
from django_communicator.services import sending_config_service

_TAGS = ["Communicator sequences"]


class SequenceView(AdminView):
    def sequence(self, channel_idx: str, pk: int) -> Sequence:
        try:
            return sending_config_service.get_sequence(self.channel(channel_idx), pk)
        except Sequence.DoesNotExist:
            raise NotFound("Sequence not found.") from None


class SequenceListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_sequences_list",
        summary="Sequences of the channel",
        responses={200: SequenceListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        rows = sending_config_service.list_sequences(self.channel(channel_idx))
        return Response(
            SequenceListResponse(results=[SequenceResponse.of(row) for row in rows]).model_dump(mode="json")
        )

    @extend_schema(
        tags=_TAGS,
        summary="Create a sequence with its steps",
        request=SequenceRequest,
        responses={201: SequenceResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(SequenceRequest, request.data)
        try:
            row = sending_config_service.create_sequence(self.channel(channel_idx), **body.model_dump())
        except sending_config_service.DuplicateSequenceError as error:
            raise Conflict(str(error), code="duplicate_sequence") from None
        return Response(SequenceResponse.of(row).model_dump(mode="json"), status=201)


class SequenceStepListView(SequenceView):
    @extend_schema(
        tags=_TAGS, summary="Steps of a sequence", responses={200: SequenceStepListResponse, **ERROR_RESPONSES}
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        steps = [SequenceStepResponse.model_validate(step) for step in self.sequence(channel_idx, pk).steps.all()]
        return Response(SequenceStepListResponse(results=steps).model_dump(mode="json"))


class TextListView(SequenceView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_sequence_texts_list",
        summary="Text pool of a sequence",
        responses={200: TextListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        rows = sending_config_service.list_texts(self.sequence(channel_idx, pk))
        return Response(
            TextListResponse(results=[TextResponse.model_validate(r) for r in rows]).model_dump(mode="json")
        )

    @extend_schema(
        tags=_TAGS,
        summary="Add a text to the pool",
        request=TextRequest,
        responses={201: TextResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(TextRequest, request.data)
        row = sending_config_service.create_text(self.sequence(channel_idx, pk), **body.model_dump())
        return Response(TextResponse.model_validate(row).model_dump(mode="json"), status=201)
