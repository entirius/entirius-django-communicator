# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — follow-up sequences, their steps and text pools."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_communicator.models import Sequence, TextPool
from django_communicator.schemas.requests import SequenceRequest, TextRequest, TextUpdateRequest
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
    access_area = "communicator.content"

    def sequence(self, channel_idx: str, pk: int) -> Sequence:
        try:
            return sending_config_service.get_sequence(self.channel(channel_idx), pk)
        except Sequence.DoesNotExist:
            raise NotFound("Sequence not found.") from None


class SequenceListView(AdminView):
    access_area = "communicator.content"

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


class TextDetailView(SequenceView):
    def text(self, channel_idx: str, pk: int, text_pk: int) -> TextPool:
        try:
            return sending_config_service.get_text(self.sequence(channel_idx, pk), text_pk)
        except TextPool.DoesNotExist:
            raise NotFound("Text not found.") from None

    @extend_schema(
        tags=_TAGS,
        summary="Edit or restore a text",
        description="Changes future follow-ups only — sent messages keep their rendered body.",
        request=TextUpdateRequest,
        responses={200: TextResponse, **ERROR_RESPONSES},
    )
    def patch(self, request: Request, channel_idx: str, pk: int, text_pk: int) -> Response:
        body = parse(TextUpdateRequest, request.data)
        text = self.text(channel_idx, pk, text_pk)
        try:
            row = sending_config_service.update_text(text, body.model_dump(exclude_none=True))
        except TextPool.DoesNotExist:
            raise NotFound("Text not found.") from None
        return Response(TextResponse.model_validate(row).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Remove a text",
        description="204: never used by a thread, deleted. 200 with the row (`is_active=false`): already used, "
        "deactivated so the thread history stays and no thread gets the same text twice.",
        request=None,
        responses={200: TextResponse, 204: None, **ERROR_RESPONSES},
    )
    def delete(self, request: Request, channel_idx: str, pk: int, text_pk: int) -> Response:
        text = self.text(channel_idx, pk, text_pk)
        if sending_config_service.remove_text(text):
            return Response(status=204)
        return Response(TextResponse.model_validate(text).model_dump(mode="json"))
