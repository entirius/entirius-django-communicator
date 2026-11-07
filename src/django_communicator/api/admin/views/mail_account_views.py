# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — mail accounts of a channel: connect a Gmail / Google Workspace mailbox over OAuth2, test it, send a
test message to the test double, disconnect. Tokens and secrets are never in a request or a response."""

from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminView, Conflict, parse
from django_communicator.enums import MailAccountState
from django_communicator.models import MailAccount
from django_communicator.schemas.requests import (
    MailAccountCreateRequest,
    MailAccountTestSendRequest,
    MailAccountUpdateRequest,
)
from django_communicator.schemas.responses import (
    MailAccountConnectResponse,
    MailAccountListResponse,
    MailAccountResponse,
    MailAccountTestResponse,
    MailAccountTestSendResponse,
)
from django_communicator.services import gmail_client, mail_account_service

_TAGS = ["Communicator Mail Accounts"]
_ERRORS = {**ERROR_RESPONSES, 409: None}


def payload(account: MailAccount) -> dict:
    connected = account.state == MailAccountState.CONNECTED
    return MailAccountResponse(
        id=account.pk,
        email=account.email,
        provider=account.provider,
        state=account.state,
        inbound_enabled=account.inbound_enabled,
        scopes=account.scopes.split(),
        can_send=connected and mail_account_service.granted(account, gmail_client.SCOPE_SEND),
        can_receive=connected
        and account.inbound_enabled
        and mail_account_service.granted(account, gmail_client.SCOPE_READ),
        connected_at=account.connected_at,
        last_ok_at=account.last_ok_at,
        last_error=account.last_error,
        last_error_at=account.last_error_at,
        last_polled_at=account.last_polled_at,
        routes=sorted(account.routes.values_list("key", flat=True)),
    ).model_dump(mode="json")


def _conflict(error: mail_account_service.MailAccountError) -> Conflict:
    return Conflict(error.client_message, code=error.code)


class _AccountView(AdminView):
    access_area = "communicator.settings"

    def account(self, channel_idx: str, pk: int) -> MailAccount:
        channel = self.channel(channel_idx)
        try:
            return mail_account_service.get_account(channel, pk)
        except MailAccount.DoesNotExist:
            raise NotFound("Mail account not found.") from None


class AccountListView(_AccountView):
    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_accounts_list",
        summary="Mail accounts of the channel",
        responses={200: MailAccountListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, channel_idx: str) -> Response:
        rows = [payload(account) for account in mail_account_service.list_accounts(self.channel(channel_idx))]
        return Response({"results": rows})

    @extend_schema(
        tags=_TAGS,
        summary="Add a mail account (not connected yet)",
        description="Adds the Google mailbox only; `accounts/{id}/connect/` starts the sign-in. 409 `ACCOUNT_EXISTS`.",
        request=MailAccountCreateRequest,
        responses={201: MailAccountResponse, **_ERRORS},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(MailAccountCreateRequest, request.data)
        try:
            account = mail_account_service.create_account(self.channel(channel_idx), **body.model_dump())
        except mail_account_service.MailAccountError as error:
            raise _conflict(error) from None
        return Response(payload(account), status=201)


class AccountDetailView(_AccountView):
    @extend_schema(tags=_TAGS, summary="One mail account", responses={200: MailAccountResponse, **ERROR_RESPONSES})
    def get(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(payload(self.account(channel_idx, pk)))

    @extend_schema(
        tags=_TAGS,
        summary="Turn reading replies on or off",
        description="Switching reading on for an account connected without the read scope needs a new sign-in "
        "(`connect/`); until then `can_receive` stays false.",
        request=MailAccountUpdateRequest,
        responses={200: MailAccountResponse, **ERROR_RESPONSES},
    )
    def patch(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(MailAccountUpdateRequest, request.data)
        account = mail_account_service.update_account(self.account(channel_idx, pk), **body.model_dump())
        return Response(payload(account))

    @extend_schema(
        tags=_TAGS,
        summary="Delete a mail account",
        description="Revokes the token at Google first. 409 `ACCOUNT_IN_USE` while a route sends through it.",
        request=None,
        responses={204: None, **_ERRORS},
    )
    def delete(self, request: Request, channel_idx: str, pk: int) -> Response:
        try:
            mail_account_service.delete_account(self.account(channel_idx, pk))
        except mail_account_service.MailAccountError as error:
            raise _conflict(error) from None
        return Response(status=204)


class AccountConnectView(_AccountView):
    @extend_schema(
        tags=_TAGS,
        summary="Start the Google sign-in",
        description="Returns the consent URL (authorization code flow, `access_type=offline`, `prompt=consent`, PKCE, "
        "signed `state`); open it in a browser. Google then calls the public callback and the account becomes "
        "`connected` - poll `accounts/{id}/`. Scopes: send, plus read only for an account with `inbound_enabled`. "
        "409 `GMAIL_NOT_CONFIGURED` while the OAuth client settings are empty.",
        request=None,
        responses={200: MailAccountConnectResponse, **_ERRORS},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        try:
            url, expires_in = mail_account_service.start_connect(self.account(channel_idx, pk))
        except mail_account_service.MailAccountError as error:
            raise _conflict(error) from None
        return Response({"authorization_url": url, "expires_in": expires_in})


class AccountDisconnectView(_AccountView):
    @extend_schema(
        tags=_TAGS,
        summary="Disconnect",
        description="Revokes the token at Google and forgets it; the routes of the account stop sending.",
        request=None,
        responses={200: MailAccountResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        return Response(payload(mail_account_service.disconnect(self.account(channel_idx, pk))))


class AccountTestView(_AccountView):
    @extend_schema(
        tags=_TAGS,
        summary="Test the connection",
        description="Refreshes the access token and, with the read scope, reads the Gmail profile and compares the "
        "address. Always 200: `ok` false carries a short `error`; a refused token sets the state `needs_reauth`.",
        request=None,
        responses={200: MailAccountTestResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        account = self.account(channel_idx, pk)
        result = mail_account_service.check_connection(account)
        account.refresh_from_db()
        return Response({**result, "account": payload(account)})


class AccountTestSendView(_AccountView):
    @extend_schema(
        tags=_TAGS,
        summary="Send a test message (test double only)",
        description="Sends one short message in the name of the account, but only while the Gmail API base URL is not "
        "Google's (the local test double). 409 `TEST_SEND_NEEDS_DOUBLE` otherwise; 409 `ACCOUNT_NOT_CONNECTED`.",
        request=MailAccountTestSendRequest,
        responses={200: MailAccountTestSendResponse, **_ERRORS},
    )
    def post(self, request: Request, channel_idx: str, pk: int) -> Response:
        body = parse(MailAccountTestSendRequest, request.data)
        try:
            message_id = mail_account_service.test_send(self.account(channel_idx, pk), to=body.to)
        except mail_account_service.MailAccountError as error:
            raise _conflict(error) from None
        return Response({"ok": True, "message_id": message_id})
