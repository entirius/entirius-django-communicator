# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 — the HTML mail footer per language; `{{ legal }}` marks where the legal text goes."""

from django_regional.models import Language
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_communicator.models import MailFooter
from django_communicator.schemas.requests import FooterRequest
from django_communicator.schemas.responses import FooterListResponse, FooterResponse
from django_communicator.services import footer_service
from django_communicator.services.template_service import find_language

_TAGS = ["Communicator footers"]


class FooterListView(AdminView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_footers_list",
        summary="Mail footers of the channel",
        responses={200: FooterListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        rows = footer_service.list_footers(self.channel(channel_idx))
        return Response(FooterListResponse(results=[FooterResponse.of(row) for row in rows]).model_dump(mode="json"))


class FooterDetailView(AdminView):
    @staticmethod
    def language(code: str) -> Language:
        language = find_language(code)
        if language is None:
            raise NotFound("Language not found.")
        return language

    def footer(self, channel_idx: str, language: str) -> MailFooter:
        try:
            return footer_service.get_footer(self.channel(channel_idx), self.language(language))
        except MailFooter.DoesNotExist:
            raise NotFound("Footer not configured.") from None

    @extend_schema(
        tags=_TAGS, summary="Mail footer in one language", responses={200: FooterResponse, **ERROR_RESPONSES}
    )
    def get(self, request: Request, channel_idx: str, language: str) -> Response:
        return Response(FooterResponse.of(self.footer(channel_idx, language)).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Create or replace the mail footer",
        description="The HTML is sanitised to an allowlist (a, img, p, br, strong, em, span, div, table family; "
        "basic text, colour and size styles) and must keep `{{ legal }}` exactly once, else 400 on `html`. "
        "Sent messages keep the footer they went out with (`footer_html`).",
        request=FooterRequest,
        responses={200: FooterResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str, language: str) -> Response:
        body = parse(FooterRequest, request.data)
        try:
            footer = footer_service.save_footer(self.channel(channel_idx), self.language(language), body.html)
        except footer_service.FooterError as error:
            raise ValidationError({"html": [str(error)]}) from None
        return Response(FooterResponse.of(footer).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Remove the mail footer",
        description="Mail in this language falls back to the channel default language's footer, else the legal "
        "text alone.",
        request=None,
        responses={204: None, **ERROR_RESPONSES},
    )
    def delete(self, request: Request, channel_idx: str, language: str) -> Response:
        footer_service.delete_footer(self.footer(channel_idx, language))
        return Response(status=204)
