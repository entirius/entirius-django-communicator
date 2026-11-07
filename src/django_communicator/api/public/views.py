# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Public one-click unsubscribe (outbound Q6, RFC 8058). No login: the signed token in the URL is the credential.

POST (a mail client's one-click request, body `List-Unsubscribe=One-Click`, or the button of the page) unsubscribes.
GET only shows a page with a button, so a link scanner that opens the URL never unsubscribes anybody.
"""

from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.decorators import method_decorator
from django.utils.html import escape
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from django_communicator import settings as communicator_settings
from django_communicator.services import mail_account_service, unsubscribe_service

_PAGE = (
    "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width'>"
    "<meta name=robots content=noindex><title>Unsubscribe</title></head><body style='font-family:sans-serif;"
    "max-width:32rem;margin:3rem auto;padding:0 1rem'>{body}</body></html>"
)


def _page(body: str, status: int = 200) -> HttpResponse:
    return HttpResponse(_PAGE.format(body=body), status=status, content_type="text/html; charset=utf-8")


@method_decorator([csrf_exempt, never_cache], name="dispatch")
class UnsubscribeView(View):
    http_method_names = ["get", "post", "head", "options"]

    def get(self, request: HttpRequest, token: str) -> HttpResponse:
        try:
            _, email = unsubscribe_service.read_token(token)
        except unsubscribe_service.InvalidTokenError:
            return _page("<p>This unsubscribe link is not valid.</p>", status=404)
        body = (
            f"<h1>Unsubscribe</h1><p>Stop e-mails to <b>{escape(email)}</b>?</p>"
            "<form method=post><button type=submit>Unsubscribe</button></form>"
        )
        return _page(body)

    def post(self, request: HttpRequest, token: str) -> HttpResponse:
        try:
            email = unsubscribe_service.unsubscribe(token)
        except unsubscribe_service.InvalidTokenError:
            return _page("<p>This unsubscribe link is not valid.</p>", status=400)
        return _page(f"<h1>Done</h1><p>{escape(email)} will not receive further e-mails from us.</p>")


@method_decorator([csrf_exempt, never_cache], name="dispatch")
class GmailCallbackView(View):
    """The redirect URI of the Google OAuth client (outbound Gmail API). The browser arrives here from Google's consent
    page; the signed `state` (one use, 10 minutes) is the credential. Shows a short result page, or sends the browser
    on to `COMMUNICATOR_GMAIL_RETURN_URL` with `?gmail=connected` / `?gmail=<error code>`."""

    http_method_names = ["get", "head", "options"]

    def get(self, request: HttpRequest) -> HttpResponse:
        state, code = request.GET.get("state", ""), request.GET.get("code", "")
        if request.GET.get("error") or not code:
            result = "consent_denied" if request.GET.get("error") else "state_invalid"
            return self._finish(result, 400)
        try:
            account = mail_account_service.complete_connect(code, state)
        except mail_account_service.MailAccountError as error:
            return self._finish(error.code, 400, error.client_message)
        return self._finish("connected", 200, f"{account.email} is connected.")

    @staticmethod
    def _finish(result: str, status: int, text: str = "") -> HttpResponse:
        target = communicator_settings.COMMUNICATOR_GMAIL_RETURN_URL
        if target:
            joiner = "&" if "?" in target else "?"
            return HttpResponseRedirect(target + joiner + urlencode({"gmail": result}))
        title = "Connected" if result == "connected" else "Not connected"
        return _page(f"<h1>{title}</h1><p>{escape(text or result)}</p><p>You can close this window.</p>", status=status)
