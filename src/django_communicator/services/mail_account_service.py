# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Mail accounts (Gmail API): CRUD, the OAuth2 consent flow, access tokens and the account state.

The refresh token lives only in `MailAccount.refresh_token` (encrypted). Short-lived access tokens are kept in the
Django cache until shortly before they expire. Nothing here logs or returns a token. A refresh token Google no longer
accepts sets the state `needs_reauth` and notifies the sales admin; the routes of that account then stop sending
(`gmail_transport.route_blocked`) until a person connects the account again.
"""

import logging
from email.message import EmailMessage

from django.core import signing
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from django_communicator.enums import MailAccountState, MailProvider
from django_communicator.models import Channel, MailAccount
from django_communicator.services import alert_service, clock_service, gmail_client
from django_communicator.services.exceptions import ClientError

logger = logging.getLogger(__name__)

STATE_SALT = "django_communicator.gmail.state"
STATE_MAX_AGE_S = 600
EXPIRY_MARGIN_S = 60


class MailAccountError(ClientError):
    """A refusal about a mail account; `code` is the machine word for the API."""

    def __init__(self, code: str, client_message: str) -> None:
        super().__init__(client_message)
        self.code = code


def _verifier_key(nonce: str) -> str:
    return f"communicator:gmail:verifier:{nonce}"


def _token_key(account: MailAccount) -> str:
    return f"communicator:gmail:access:{account.pk}"


def list_accounts(channel: Channel):
    return MailAccount.objects.filter(channel=channel).order_by("email")


def get_account(channel: Channel, pk: int) -> MailAccount:
    """Raises `MailAccount.DoesNotExist`."""
    return MailAccount.objects.get(channel=channel, pk=pk)


def create_account(channel: Channel, *, email: str, inbound_enabled: bool = False) -> MailAccount:
    try:
        with transaction.atomic():
            return MailAccount.objects.create(
                channel=channel,
                email=email.strip().lower(),
                provider=MailProvider.GMAIL,
                inbound_enabled=inbound_enabled,
            )
    except IntegrityError:
        raise MailAccountError("account_exists", f"mail account {email} already exists") from None


def update_account(account: MailAccount, *, inbound_enabled: bool) -> MailAccount:
    account.inbound_enabled = inbound_enabled
    account.save(update_fields=["inbound_enabled", "modified_at"])
    return account


def delete_account(account: MailAccount) -> None:
    """Revokes the token first. Raises `MailAccountError` while a route still sends through the account."""
    if account.routes.exists():
        raise MailAccountError("account_in_use", f"mail account {account.email} is used by a sender route")
    if account.refresh_token:
        gmail_client.revoke(account.refresh_token)
    cache.delete(_token_key(account))
    account.delete()


def scopes_for(account: MailAccount) -> list[str]:
    """Send always; read the inbox only when the account is meant to receive replies (the least that works)."""
    scopes = [*gmail_client.IDENTITY_SCOPES, gmail_client.SCOPE_SEND]
    return [*scopes, gmail_client.SCOPE_READ] if account.inbound_enabled else scopes


def granted(account: MailAccount, scope: str) -> bool:
    return scope in account.scopes.split()


# --- OAuth2 consent -------------------------------------------------------------------------------------------------


def start_connect(account: MailAccount) -> tuple[str, int]:
    """(Google consent URL, seconds it stays valid). Raises `MailAccountError` `gmail_not_configured`."""
    if not gmail_client.configured():
        raise MailAccountError(
            "gmail_not_configured",
            "set COMMUNICATOR_GMAIL_CLIENT_ID, COMMUNICATOR_GMAIL_CLIENT_SECRET and COMMUNICATOR_GMAIL_REDIRECT_URI",
        )
    verifier = gmail_client.new_verifier()
    state = signing.dumps({"a": account.pk}, salt=STATE_SALT, compress=True)
    cache.set(_verifier_key(state), verifier, STATE_MAX_AGE_S)
    url = gmail_client.authorization_url(
        state=state, login_hint=account.email, scopes=scopes_for(account), verifier=verifier
    )
    return url, STATE_MAX_AGE_S


def complete_connect(code: str, state: str) -> MailAccount:
    """The OAuth callback: exchange the code, check who signed in, store the encrypted refresh token.

    Raises `MailAccountError` (`state_invalid`, `account_mismatch`, `no_refresh_token`, `scope_missing`,
    `exchange_failed`). A refused connect leaves the account as it was, except for `last_error`."""
    try:
        account_id = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE_S)["a"]
        account = MailAccount.objects.select_related("channel").get(pk=account_id)
    except (signing.BadSignature, KeyError, TypeError, MailAccount.DoesNotExist):
        raise MailAccountError("state_invalid", "the sign-in link is invalid or expired") from None
    verifier = cache.get(_verifier_key(state))
    if not verifier:
        raise MailAccountError("state_invalid", "the sign-in link was already used or expired")
    cache.delete(_verifier_key(state))
    try:
        tokens = gmail_client.exchange_code(code, verifier)
    except gmail_client.GmailError as error:
        _record_error(account, f"exchange_{error.code}")
        raise MailAccountError("exchange_failed", "Google did not accept the sign-in") from None
    if tokens.email != account.email.lower():
        if tokens.refresh_token:
            gmail_client.revoke(tokens.refresh_token)
        _record_error(account, "account_mismatch")
        raise MailAccountError("account_mismatch", f"signed in as a different Google account than {account.email}")
    if not tokens.refresh_token:
        _record_error(account, "no_refresh_token")
        raise MailAccountError("no_refresh_token", "Google sent no refresh token; remove the app access and retry")
    if gmail_client.SCOPE_SEND not in tokens.scope.split():
        gmail_client.revoke(tokens.refresh_token)
        _record_error(account, "scope_missing")
        raise MailAccountError("scope_missing", "the permission to send mail was not granted")
    now = timezone.now()
    account.refresh_token, account.scopes = tokens.refresh_token, tokens.scope
    account.state, account.connected_at, account.last_ok_at = MailAccountState.CONNECTED, now, now
    account.last_error, account.last_error_at = "", None
    account.history_id = ""
    account.save()
    cache.set(_token_key(account), tokens.access_token, max(tokens.expires_in - EXPIRY_MARGIN_S, 1))
    if granted(account, gmail_client.SCOPE_READ):
        start_cursor(account, tokens.access_token)
    return account


def start_cursor(account: MailAccount, access_token: str) -> None:
    """Replies are read from now on: the cursor starts at the mailbox's current history id."""
    try:
        history_id = str(gmail_client.profile(access_token).get("historyId") or "")
    except gmail_client.GmailError as error:
        logger.warning("communicator mail account %s: no history id (%s)", account.pk, error.code)
        return
    MailAccount.objects.filter(pk=account.pk).update(history_id=history_id)
    account.history_id = history_id


def disconnect(account: MailAccount) -> MailAccount:
    """Revoke at Google (best effort), forget the token. The routes of the account stop sending."""
    if account.refresh_token:
        gmail_client.revoke(account.refresh_token)
    cache.delete(_token_key(account))
    account.refresh_token, account.scopes, account.history_id = "", "", ""
    account.state, account.connected_at = MailAccountState.NOT_CONNECTED, None
    account.last_error, account.last_error_at = "", None
    account.save()
    return account


# --- tokens and state -----------------------------------------------------------------------------------------------


def _record_error(account: MailAccount, code: str) -> None:
    now = timezone.now()
    MailAccount.objects.filter(pk=account.pk).update(last_error=code[:255], last_error_at=now)
    account.last_error, account.last_error_at = code[:255], now


def mark_needs_reauth(account: MailAccount, code: str) -> None:
    """The refresh token no longer works: state, forget the cached access token, tell the sales admin (once a day)."""
    if account.state == MailAccountState.NEEDS_REAUTH:
        return
    now = timezone.now()
    MailAccount.objects.filter(pk=account.pk).update(
        state=MailAccountState.NEEDS_REAUTH, last_error=code[:255], last_error_at=now, modified_at=now
    )
    account.state, account.last_error, account.last_error_at = MailAccountState.NEEDS_REAUTH, code[:255], now
    cache.delete(_token_key(account))
    logger.warning("communicator mail account %s needs a new sign-in (%s)", account.pk, code)
    channel = account.channel
    alert_service.notify_once(
        channel,
        kind=f"mail_account_reauth_{account.pk}",
        severity="high",
        title=f"Mailbox {account.email} must be connected again; its routes stop sending",
        day=clock_service.now_for(channel).date(),
    )


def access_token(account: MailAccount, *, force: bool = False) -> str:
    """A valid access token. Raises `GmailAuthError` (state set to needs_reauth when Google refused the refresh
    token, or when the account is not connected) and `GmailTransientError`."""
    if account.state != MailAccountState.CONNECTED or not account.refresh_token:
        # An undecryptable token reads as "" (SECRET_KEY changed): same as revoked.
        if account.state == MailAccountState.CONNECTED:
            mark_needs_reauth(account, "token_unreadable")
        raise gmail_client.GmailAuthError("not_connected")
    if not force and (cached := cache.get(_token_key(account))):
        return cached
    try:
        token, expires_in = gmail_client.refresh(account.refresh_token)
    except gmail_client.GmailAuthError as error:
        mark_needs_reauth(account, error.code)
        raise
    cache.set(_token_key(account), token, max(expires_in - EXPIRY_MARGIN_S, 1))
    MailAccount.objects.filter(pk=account.pk).update(last_ok_at=timezone.now())
    return token


def with_token(account: MailAccount, call):
    """`call(access_token)`; a 401 refreshes the token once and repeats the call."""
    try:
        return call(access_token(account))
    except gmail_client.GmailTokenRejected:
        return call(access_token(account, force=True))


def check_connection(account: MailAccount) -> dict:
    """Refresh the token and, when a read scope was granted, read the profile. Never raises for a provider problem:
    `{ok, checks, error}`; the state of the account is updated as a side effect."""
    checks = []
    try:
        access_token(account, force=True)
        checks.append("token")
        if granted(account, gmail_client.SCOPE_READ):
            profile = with_token(account, gmail_client.profile)
            if str(profile.get("emailAddress", "")).lower() != account.email.lower():
                _record_error(account, "profile_mismatch")
                return {"ok": False, "checks": checks, "error": "profile_mismatch"}
            checks.append("profile")
    except gmail_client.GmailError as error:
        if not isinstance(error, gmail_client.GmailAuthError):
            _record_error(account, error.code)
        return {"ok": False, "checks": checks, "error": error.code}
    MailAccount.objects.filter(pk=account.pk).update(last_ok_at=timezone.now(), last_error="", last_error_at=None)
    account.last_error = ""
    return {"ok": True, "checks": checks, "error": ""}


def test_send(account: MailAccount, *, to: str) -> str:
    """One short message in the name of the account, to the test double only. Raises `MailAccountError`."""
    if gmail_client.is_real_google():
        raise MailAccountError("test_send_needs_double", "a test message is only sent to the local Gmail test double")
    if account.state != MailAccountState.CONNECTED:
        raise MailAccountError("account_not_connected", f"mail account {account.email} is not connected")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = account.email, to, "[TEST] communicator mail account"
    message.set_content("Test message from the communicator. Nothing was sent to a real recipient.")
    try:
        return with_token(account, lambda token: gmail_client.send(token, message.as_bytes()))
    except gmail_client.GmailError as error:
        _record_error(account, error.code)
        raise MailAccountError("send_failed", f"the test message was refused ({error.code})") from None


def pollable_accounts():
    """Connected accounts that read replies and hold the read scope."""
    return [
        account
        for account in MailAccount.objects.select_related("channel").filter(
            inbound_enabled=True, state=MailAccountState.CONNECTED
        )
        if granted(account, gmail_client.SCOPE_READ)
    ]
