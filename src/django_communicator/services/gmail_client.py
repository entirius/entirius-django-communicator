# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The few Gmail API / Google OAuth calls the communicator uses, over httpx. No database, no logging of bodies.

Every URL comes from `COMMUNICATOR_GMAIL_*` settings, so tests point them at `tests/gmail_stub.py`. A token, a code
or a response body never reaches an exception text or a log line: errors carry a short `code` (Google's `error` /
`reason`) and the HTTP status only.
"""

import base64
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx

from django_communicator import settings as communicator_settings

logger = logging.getLogger(__name__)

SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPE_READ = "https://www.googleapis.com/auth/gmail.readonly"
IDENTITY_SCOPES = ("openid", "email")
RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded", "backendError"})
USER = "users/me"


class GmailError(Exception):
    """A failed call. `code` is a short machine word (never a token); `status` the HTTP status or 0."""

    def __init__(self, code: str, status: int = 0) -> None:
        super().__init__(f"{code} ({status})" if status else code)
        self.code = code
        self.status = status


class GmailAuthError(GmailError):
    """The refresh token was refused (revoked, expired, client changed): the account needs a new consent."""


class GmailTokenRejected(GmailError):
    """The access token was refused (401): refresh it once and retry."""


class GmailTransientError(GmailError):
    """Try again later: network, 429, 5xx, rate limit."""


class GmailUnknownOutcome(GmailError):
    """The request may have reached Google (read timeout, dropped connection): a send must not be repeated."""


class GmailRequestError(GmailError):
    """Google refused the request for good (bad recipient, missing scope, ...)."""


class GmailNotFound(GmailError):
    """404 (a message deleted since it was listed, or a history id that expired)."""


@dataclass(frozen=True)
class TokenSet:
    refresh_token: str
    access_token: str
    expires_in: int
    scope: str
    email: str


def is_real_google() -> bool:
    """True while the API base is Google's own: nothing outside production flows may call it then."""
    host = urlsplit(communicator_settings.COMMUNICATOR_GMAIL_API_BASE).hostname or ""
    return host == "googleapis.com" or host.endswith(".googleapis.com") or host.endswith(".google.com")


def configured() -> bool:
    s = communicator_settings
    return bool(
        s.COMMUNICATOR_GMAIL_CLIENT_ID and s.COMMUNICATOR_GMAIL_CLIENT_SECRET and s.COMMUNICATOR_GMAIL_REDIRECT_URI
    )


def new_verifier() -> str:
    return secrets.token_urlsafe(48)


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def authorization_url(*, state: str, login_hint: str, scopes: list[str], verifier: str) -> str:
    query = {
        "client_id": communicator_settings.COMMUNICATOR_GMAIL_CLIENT_ID,
        "redirect_uri": communicator_settings.COMMUNICATOR_GMAIL_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
        "login_hint": login_hint,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    }
    return communicator_settings.COMMUNICATOR_GMAIL_AUTH_URL + "?" + urlencode(query)


def _client() -> httpx.Client:
    return httpx.Client(timeout=communicator_settings.COMMUNICATOR_GMAIL_TIMEOUT_S, follow_redirects=False)


def _reason(response: httpx.Response) -> str:
    """Google's short error word from a JSON error body; nothing else of the body is kept."""
    try:
        body = response.json()
    except ValueError:
        return "http_error"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, str):
        return error[:64]
    if isinstance(error, dict):
        errors = error.get("errors") or []
        if errors and isinstance(errors[0], dict) and errors[0].get("reason"):
            return str(errors[0]["reason"])[:64]
        return str(error.get("status") or "http_error")[:64]
    return "http_error"


def _raise_for(response: httpx.Response) -> None:
    status, reason = response.status_code, _reason(response)
    if status == 401:
        raise GmailTokenRejected("unauthorized", status)
    if status == 404:
        raise GmailNotFound("not_found", status)
    if status == 429 or status >= 500 or (status == 403 and reason in RATE_LIMIT_REASONS):
        raise GmailTransientError(reason if status != 429 else "rate_limited", status)
    raise GmailRequestError(reason, status)


def _call(method: str, url: str, *, send: bool = False, **kwargs) -> httpx.Response:
    try:
        with _client() as client:
            response = client.request(method, url, **kwargs)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise GmailTransientError("connect_failed") from None
    except httpx.TransportError:
        raise (GmailUnknownOutcome("no_answer") if send else GmailTransientError("no_answer")) from None
    if response.status_code >= 400:
        _raise_for(response)
    return response


def _token_request(data: dict) -> dict:
    data = {
        **data,
        "client_id": communicator_settings.COMMUNICATOR_GMAIL_CLIENT_ID,
        "client_secret": communicator_settings.COMMUNICATOR_GMAIL_CLIENT_SECRET,
    }
    try:
        response = _call("POST", communicator_settings.COMMUNICATOR_GMAIL_TOKEN_URL, data=data)
    except GmailRequestError as error:
        if error.code in {"invalid_grant", "invalid_client", "unauthorized_client"}:
            raise GmailAuthError(error.code, error.status) from None
        raise
    return response.json()


def _id_token_email(id_token: str) -> str:
    """The `email` claim of the id token Google returned over TLS for this very request (no signature check needed)."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return str(claims.get("email") or "").strip().lower()
    except (IndexError, ValueError, AttributeError):
        return ""


def exchange_code(code: str, verifier: str) -> TokenSet:
    body = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": communicator_settings.COMMUNICATOR_GMAIL_REDIRECT_URI,
            "code_verifier": verifier,
        }
    )
    return TokenSet(
        refresh_token=str(body.get("refresh_token") or ""),
        access_token=str(body.get("access_token") or ""),
        expires_in=int(body.get("expires_in") or 0),
        scope=str(body.get("scope") or ""),
        email=_id_token_email(str(body.get("id_token") or "")),
    )


def refresh(refresh_token: str) -> tuple[str, int]:
    """(access token, seconds valid). Raises `GmailAuthError` when the refresh token is no longer accepted."""
    body = _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
    return str(body["access_token"]), int(body.get("expires_in") or 3000)


def revoke(token: str) -> bool:
    """Best effort: True when Google accepted the revocation."""
    try:
        _call("POST", communicator_settings.COMMUNICATOR_GMAIL_REVOKE_URL, data={"token": token})
    except GmailError as error:
        logger.warning("communicator gmail revoke failed: %s", error.code)
        return False
    return True


def _url(path: str) -> str:
    return f"{communicator_settings.COMMUNICATOR_GMAIL_API_BASE.rstrip('/')}/gmail/v1/{USER}/{path}"


def _auth(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def profile(access_token: str) -> dict:
    """`{emailAddress, historyId, ...}`; needs a read scope."""
    return _call("GET", _url("profile"), headers=_auth(access_token)).json()


def send(access_token: str, raw: bytes) -> str:
    """`users.messages.send` with the RFC 822 message; the Gmail message id."""
    payload = {"raw": base64.urlsafe_b64encode(raw).decode()}
    response = _call("POST", _url("messages/send"), send=True, headers=_auth(access_token), json=payload)
    return str(response.json().get("id", ""))


def list_messages(access_token: str, *, query: str = "", label: str = "", limit: int = 50) -> list[str]:
    params: dict = {"maxResults": limit}
    if query:
        params["q"] = query
    if label:
        params["labelIds"] = label
    body = _call("GET", _url("messages"), headers=_auth(access_token), params=params).json()
    return [str(item["id"]) for item in body.get("messages", [])]


def get_raw(access_token: str, message_id: str) -> bytes:
    body = _call("GET", _url(f"messages/{message_id}"), headers=_auth(access_token), params={"format": "raw"}).json()
    raw = str(body.get("raw") or "")
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))


def history(access_token: str, start_history_id: str, *, label: str = "INBOX") -> tuple[list[tuple[str, str]], str]:
    """([(history record id, message id)] of messages added to `label` after `start_history_id`, oldest first and
    without repeats; the newest history id of the mailbox), all pages."""
    found: dict[str, str] = {}
    newest, page = start_history_id, ""
    while True:
        params = {"startHistoryId": start_history_id, "historyTypes": "messageAdded", "labelId": label}
        if page:
            params["pageToken"] = page
        body = _call("GET", _url("history"), headers=_auth(access_token), params=params).json()
        for entry in body.get("history", []):
            for added in entry.get("messagesAdded", []):
                found.setdefault(str(added["message"]["id"]), str(entry.get("id") or newest))
        newest = str(body.get("historyId") or newest)
        page = body.get("nextPageToken", "")
        if not page:
            return [(record, message) for message, record in found.items()], newest
