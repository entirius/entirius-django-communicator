# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 — the HTML mail footer per language and brand; `{{ legal }}` marks where the legal text goes,
`{{ unsubscribe }}` (optional) where the unsubscribe link goes. `?brand=` picks a brand's footer; without it the
channel-wide one (blank brand)."""

from django_regional.models import Language
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, parse
from django_communicator.models import MailFooter
from django_communicator.schemas.requests import FooterQuery, FooterRequest
from django_communicator.schemas.responses import FooterListResponse, FooterResponse
from django_communicator.services import footer_service
from django_communicator.services.template_service import find_language

_TAGS = ["Communicator Footers"]
_BRAND = OpenApiParameter(
    "brand", str, description="Brand of the footer (slug); omitted or empty: the channel-wide footer."
)


class FooterListView(AdminView):
    access_area = "communicator.content"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_footers_list",
        summary="Mail footers of the channel (every language and brand)",
        responses={200: FooterListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        rows = footer_service.list_footers(self.channel(channel_idx))
        return Response(FooterListResponse(results=[FooterResponse.of(row) for row in rows]).model_dump(mode="json"))


class FooterDetailView(AdminView):
    access_area = "communicator.content"

    @staticmethod
    def language(code: str) -> Language:
        language = find_language(code)
        if language is None:
            raise NotFound("Language not found.")
        return language

    @staticmethod
    def brand(request: Request) -> str:
        return parse(FooterQuery, request.query_params.dict()).brand

    def footer(self, request: Request, channel_idx: str, language: str) -> MailFooter:
        brand = self.brand(request)
        try:
            return footer_service.get_footer(self.channel(channel_idx), self.language(language), brand)
        except MailFooter.DoesNotExist:
            raise NotFound("Footer not configured.") from None

    @extend_schema(
        tags=_TAGS,
        summary="Mail footer in one language",
        parameters=[_BRAND],
        responses={200: FooterResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str, language: str) -> Response:
        return Response(FooterResponse.of(self.footer(request, channel_idx, language)).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Create or replace the mail footer",
        description="The HTML is sanitised to an allowlist (a, img, p, br, strong, em, span, div, table family; "
        "basic text, colour and size styles) and must keep `{{ legal }}` exactly once and `{{ unsubscribe }}` at "
        "most once, both as text, else 400 on `html`. Sent messages keep the footer they went out with "
        "(`footer_html`). A mail takes the footer of (its language, its mailbox brand), else (language, no brand), "
        "else the channel default language with the brand, then without.",
        parameters=[_BRAND],
        request=FooterRequest,
        responses={200: FooterResponse, **ERROR_RESPONSES},
    )
    def put(self, request: Request, channel_idx: str, language: str) -> Response:
        body = parse(FooterRequest, request.data)
        brand = self.brand(request)
        channel, code = self.channel(channel_idx), self.language(language)
        try:
            footer = footer_service.save_footer(channel, code, body.html, brand, body.unsubscribe_label)
        except footer_service.FooterError as error:
            raise ValidationError({"html": [error.client_message]}) from None
        return Response(FooterResponse.of(footer).model_dump(mode="json"))

    @extend_schema(
        tags=_TAGS,
        summary="Remove the mail footer",
        description="Mail in this language falls back to the channel-wide footer, then the channel default "
        "language's, else the legal text alone.",
        parameters=[_BRAND],
        request=None,
        responses={204: None, **ERROR_RESPONSES},
    )
    def delete(self, request: Request, channel_idx: str, language: str) -> Response:
        footer_service.delete_footer(self.footer(request, channel_idx, language))
        return Response(status=204)
