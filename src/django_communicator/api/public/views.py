# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Public one-click unsubscribe (RFC 8058). No login: the signed token in the URL is the credential.

POST (a mail client's one-click request with body `List-Unsubscribe=One-Click`, or the button of the page)
unsubscribes. GET only shows the page with the button, so a link scanner that opens the URL unsubscribes nobody.
"""

from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.views import APIView

from django_communicator.api.public.throttling import UnsubscribeThrottle
from django_communicator.models import Channel
from django_communicator.services import unsubscribe_service

_TAGS = ["Communicator Public"]
_TEMPLATE = "django_communicator/unsubscribe.html"
_HTML = {
    (200, "text/html"): OpenApiResponse(OpenApiTypes.STR, description="The page."),
    (400, "text/html"): OpenApiResponse(OpenApiTypes.STR, description="Invalid link page (one generic message)."),
    429: OpenApiResponse(description="Throttled."),
}
_TEXTS = {
    "pl": {
        "lang": "pl",
        "title": "Wypisz się",
        "ask": "Czy chcesz przestać otrzymywać od nas wiadomości na adres",
        "button": "Wypisz mnie",
        "done": "Gotowe. Nie wyślemy już wiadomości na adres",
        "invalid": "Ten link do wypisania się jest nieprawidłowy.",
    },
    "en": {
        "lang": "en",
        "title": "Unsubscribe",
        "ask": "Do you want to stop receiving our e-mails at",
        "button": "Unsubscribe",
        "done": "Done. We will not send further e-mails to",
        "invalid": "This unsubscribe link is not valid.",
    },
}


def _texts(channel: Channel | None) -> dict[str, str]:
    language = channel.default_language.iso2.lower() if channel and channel.default_language else ""
    return _TEXTS.get(language, _TEXTS["en"])


@method_decorator(never_cache, name="dispatch")
class UnsubscribeView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [UnsubscribeThrottle]

    @extend_schema(tags=_TAGS, summary="Unsubscribe confirmation page (changes nothing)", responses=_HTML)
    def get(self, request: Request, token: str):
        try:
            channel, email = unsubscribe_service.read_token(token)
        except unsubscribe_service.InvalidTokenError:
            return _page(request, None, {"state": "invalid"}, status=400)
        return _page(request, channel, {"state": "ask", "email": email})

    @extend_schema(
        tags=_TAGS,
        summary="Unsubscribe the address of the link (one-click, idempotent)",
        request=None,
        responses=_HTML,
    )
    def post(self, request: Request, token: str):
        try:
            channel, email = unsubscribe_service.unsubscribe(token)
        except unsubscribe_service.InvalidTokenError:
            return _page(request, None, {"state": "invalid"}, status=400)
        return _page(request, channel, {"state": "done", "email": email})


def _page(request: Request, channel: Channel | None, context: dict, status: int = 200):
    return render(request, _TEMPLATE, {**context, "t": _texts(channel)}, status=status)
