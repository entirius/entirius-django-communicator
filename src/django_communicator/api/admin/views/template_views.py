# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — message templates: list, create, detail, replace, versions, test-generate, toolbox models."""

from django.core.exceptions import ValidationError as DjangoValidationError
from django_utils.toolbox import ToolboxClient, ToolboxError, handle_toolbox_error
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_communicator.models import MessageTemplate
from django_communicator.schemas.requests import TemplateRequest, TestGenerateRequest
from django_communicator.schemas.responses import (
    DraftPreviewResponse,
    ModelListResponse,
    TemplateListResponse,
    TemplateResponse,
    TemplateVersionListResponse,
    TemplateVersionResponse,
)
from django_communicator.services import preview_service, template_service
from django_communicator.services.drafting_service import DraftOutputError
from django_communicator.services.render_service import RenderError

_TAGS = ["Communicator templates"]


class DraftOutputInvalid(APIException):
    status_code = 502
    default_detail = "The model output does not match {subject, body_paragraphs}."
    default_code = "schema_invalid"


def _save(template: MessageTemplate, body: TemplateRequest, user) -> dict:
    """Apply the whitelisted request fields and save through the versioning service."""
    language = template_service.find_language(body.language)
    if language is None:
        raise ValidationError({"language": ["Unknown language code."]})
    for field, value in body.model_dump(exclude={"language"}).items():
        setattr(template, field, value)
    template.language = language
    try:
        template_service.save_template(template, user=user)
    except DjangoValidationError as error:
        raise ValidationError(
            error.message_dict if hasattr(error, "error_dict") else {"non_field_errors": error.messages}
        ) from None
    return TemplateResponse.of(template).model_dump(mode="json")


class TemplateListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_templates_list",
        summary="List templates of the channel",
        responses={200: TemplateListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        templates = template_service.list_templates(self.channel(channel_idx))
        return Response(
            TemplateListResponse(results=[TemplateResponse.of(t) for t in templates]).model_dump(mode="json")
        )

    @extend_schema(
        tags=_TAGS,
        summary="Create a template (version 1)",
        request=TemplateRequest,
        responses={201: TemplateResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(TemplateRequest, request.data)
        template = MessageTemplate(channel=self.channel(channel_idx))
        return Response(_save(template, body, request.user), status=201)


class _TemplateObjectView(AdminView):
    def template(self, channel_idx: str, pk: int) -> MessageTemplate:
        try:
            return template_service.get_template(self.channel(channel_idx), pk)
        except MessageTemplate.DoesNotExist:
            raise NotFound("Template not found.") from None


class TemplateDetailView(_TemplateObjectView):
    @extend_schema(tags=_TAGS, summary="Get one template", responses={200: TemplateResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(TemplateResponse.of(self.template(channel_idx, pk)).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Replace a template",
        description="A change of subject, body, json_schema or model creates the next version; "
        "existing drafts keep the version they were rendered with.",
        request=TemplateRequest,
        responses={200: TemplateResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(TemplateRequest, request.data)
        return Response(_save(self.template(channel_idx, pk), body, request.user))


class TemplateVersionsView(_TemplateObjectView):
    @extend_schema(
        tags=_TAGS,
        summary="List versions of a template",
        responses={200: TemplateVersionListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        versions = self.template(channel_idx, pk).versions.all()
        results = [TemplateVersionResponse.model_validate(version) for version in versions]
        return Response(TemplateVersionListResponse(results=results).model_dump(mode="json"))


class TemplateTestGenerateView(_TemplateObjectView):
    @extend_schema(
        tags=_TAGS,
        summary="Draft with a sample context without saving",
        description="Static templates only render; ai_prompt templates call the toolbox "
        "(tag `communicator.test_generate`). Toolbox errors keep their v2 status and code.",
        request=TestGenerateRequest,
        responses={200: DraftPreviewResponse, **ERROR_RESPONSES, 402: None, 502: None, 503: None, 504: None},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(TestGenerateRequest, request.data or {})
        template = self.template(channel_idx, pk)
        try:
            preview = preview_service.generate_preview(template, body.context)
        except RenderError as error:
            raise ValidationError({"context": [str(error)]}) from None
        except ToolboxError as error:
            return handle_toolbox_error(error)
        except DraftOutputError:
            raise DraftOutputInvalid() from None
        return Response(DraftPreviewResponse(**preview).model_dump(mode="json"))


class ModelListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        summary="Toolbox models available for templates",
        responses={200: ModelListResponse, **ERROR_RESPONSES, 502: None, 503: None},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        self.channel(channel_idx)
        try:
            with ToolboxClient() as client:
                models = client.list_models()
        except ToolboxError as error:
            return handle_toolbox_error(error)
        return Response(ModelListResponse(results=models).model_dump(mode="json"))
