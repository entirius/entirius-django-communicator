# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Mailboxes of a channel: CRUD, which mailbox a thread sends from, state, the per-mailbox cap with its warm-up and
the logins. The only module that reads mailbox passwords; they never reach a log line, an exception text, an API
answer or a notification.

A stored password that cannot be decrypted (SECRET_KEY changed) reads as empty: every write names its fields and
leaves a password column alone unless the caller sends a new password, so an unrelated save never wipes it.
"""

import copy
import imaplib
import smtplib
import ssl
from datetime import date, datetime
from email.utils import formataddr
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from django_communicator.enums import MailboxState, SmtpSecurity
from django_communicator.models import Channel, Mailbox, Message, Thread
from django_communicator.services import alert_service, clock_service, footer_service
from django_communicator.services.exceptions import ClientError

PASSWORD_FIELDS = ("smtp_password", "imap_password")
EDITABLE_FIELDS = frozenset(
    {
        "address",
        "display_name",
        "auth_method",
        "smtp_host",
        "smtp_port",
        "smtp_security",
        "smtp_user",
        "send_enabled",
        "imap_host",
        "imap_port",
        "imap_use_ssl",
        "imap_user",
        "folder",
        "receive_enabled",
        "daily_cap",
        "warmup_start_cap",
        "warmup_step",
        "warmup_started_on",
    }
)
COPY_FIELDS = ("cc_addresses", "bcc_addresses")
COPIES_MAX = 10
_CHECKED_FIELDS = frozenset({*COPY_FIELDS, "brand", "signature_html"})
_RELATION_FIELDS = frozenset({"user_id", "is_default", "is_user_default", *PASSWORD_FIELDS})
IMAP_IDENTITY = ("imap_host", "imap_user", "folder")
LOGIN_TIMEOUT_S = 30
IMAP_ERROR = imaplib.IMAP4.error  # bound at import: a test may replace imaplib.IMAP4
OK, NOT_CONFIGURED, NO_PASSWORD, AUTH_FAILED, UNREACHABLE, CERTIFICATE = (
    "ok",
    "not_configured",
    "no_password",
    "auth_failed",
    "unreachable",
    "certificate",
)


class MailboxError(ClientError):
    """A mailbox change the API client must correct."""


class MailboxInUseError(MailboxError):
    """The mailbox is the channel default or threads send from it."""


class MailboxLoginError(Exception):
    """The mailbox has no usable login (host or password missing). Never carries a credential."""


def list_mailboxes(channel: Channel) -> QuerySet[Mailbox]:
    return Mailbox.objects.filter(channel=channel).order_by("address", "pk")


def get_mailbox(channel: Channel, pk: int) -> Mailbox:
    """Raises `Mailbox.DoesNotExist` when the mailbox is not in this channel."""
    return Mailbox.objects.get(channel=channel, pk=pk)


def create_mailbox(channel: Channel, changes: dict[str, Any]) -> Mailbox:
    """Raises `MailboxError` for a taken address, a warm-up without a ceiling, an unknown user, an invalid copy
    address, brand or signature. A user's first mailbox becomes that user's default."""
    mailbox = Mailbox(channel=channel)
    _apply(mailbox, changes)
    return _saved(mailbox, None, explicit_user_default="is_user_default" in changes)


def update_mailbox(mailbox: Mailbox, changes: dict[str, Any]) -> Mailbox:
    """Only the named fields are written; a password column only when `changes` carries a new password."""
    fields = _apply(mailbox, changes)
    return _saved(mailbox, fields, explicit_user_default="is_user_default" in changes)


def check_changes(mailbox: Mailbox, changes: dict[str, Any]) -> None:
    """Raise what `update_mailbox` / `create_mailbox` would raise for `changes`, without writing anything."""
    _apply(copy.copy(mailbox), changes)


@transaction.atomic
def delete_mailbox(mailbox: Mailbox) -> None:
    """Raises `MailboxInUseError` while it is the default or a thread sends from it. When it was its user's default,
    the user's oldest other mailbox takes the flag."""
    if mailbox.is_default:
        raise MailboxInUseError("the default mailbox cannot be deleted; choose another default first")
    if Thread.objects.filter(mailbox=mailbox).exists():
        raise MailboxInUseError("threads send from this mailbox; disable it instead")
    channel_id, user_id, was_user_default = mailbox.channel_id, mailbox.user_id, mailbox.is_user_default
    mailbox.delete()
    if was_user_default:
        _promote_user_default(channel_id, user_id)


def _apply(mailbox: Mailbox, changes: dict[str, Any]) -> list[str]:
    """Set the changes on the instance; the names of the columns to write."""
    invalid = set(changes) - EDITABLE_FIELDS - _CHECKED_FIELDS - _RELATION_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable on a mailbox: {sorted(invalid)}")
    identity = [getattr(mailbox, name) for name in IMAP_IDENTITY]
    fields = sorted(EDITABLE_FIELDS & set(changes))
    for name in fields:
        setattr(mailbox, name, changes[name])
    fields += _set_checked(mailbox, changes) + _set_passwords(mailbox, changes) + _set_relations(mailbox, changes)
    if mailbox.pk and identity != [getattr(mailbox, name) for name in IMAP_IDENTITY]:
        mailbox.last_uid, mailbox.uidvalidity = 0, None  # another mailbox: the cursor starts over
        fields += ["last_uid", "uidvalidity"]
    return [*fields, *_check_warmup(mailbox)]


def _set_checked(mailbox: Mailbox, changes: dict[str, Any]) -> list[str]:
    """Copy lists (each replaced as a whole), brand and signature, normalised or refused."""
    fields = sorted(_CHECKED_FIELDS & set(changes))
    for name in fields:
        value = changes[name]
        if name in COPY_FIELDS:
            value = _addresses(name, value)
        elif name == "brand":
            try:
                value = footer_service.clean_brand(value)
            except footer_service.BrandError as error:
                raise MailboxError(error.client_message) from None
        else:
            try:
                value = footer_service.clean_signature(value)
            except footer_service.FooterError as error:
                raise MailboxError(error.client_message) from None
        setattr(mailbox, name, value)
    return fields


def _addresses(name: str, values: list[str] | None) -> list[str]:
    """Trimmed, lower-cased, without duplicates (first one kept), each a valid address, at most `COPIES_MAX`."""
    cleaned = list(dict.fromkeys(value.strip().lower() for value in values or [] if value and value.strip()))
    for address in cleaned:
        try:
            validate_email(address)
        except ValidationError:
            raise MailboxError(f"{name}: {address!r} is not a valid e-mail address") from None
    if len(cleaned) > COPIES_MAX:
        raise MailboxError(f"{name}: at most {COPIES_MAX} addresses")
    return cleaned


def _set_passwords(mailbox: Mailbox, changes: dict[str, Any]) -> list[str]:
    """SMTP and IMAP logins are ASCII: another password could never log in (and would raise in smtplib)."""
    fields = [name for name in PASSWORD_FIELDS if changes.get(name)]
    if not all(changes[name].isascii() for name in fields):
        raise MailboxError("a password may contain ASCII characters only")
    for name in fields:
        setattr(mailbox, name, changes[name])
    if "smtp_password" in fields:
        mailbox.smtp_login_failed_at = None
        fields.append("smtp_login_failed_at")
    return fields


def _set_relations(mailbox: Mailbox, changes: dict[str, Any]) -> list[str]:
    fields = []
    if "user_id" in changes:
        mailbox.user_id = _staff_id(changes["user_id"])
        fields.append("user")
    if "is_default" in changes:
        mailbox.is_default = bool(changes["is_default"])
        fields.append("is_default")
    if "is_user_default" in changes:
        mailbox.is_user_default = bool(changes["is_user_default"])
        fields.append("is_user_default")
    return fields


def _staff_id(user_id: int | None) -> int | None:
    if user_id is not None and not get_user_model().objects.filter(pk=user_id, is_staff=True).exists():
        raise MailboxError(f"user {user_id} is not a staff member")
    return user_id


def _check_warmup(mailbox: Mailbox) -> list[str]:
    """A warm-up needs a ceiling; one without a start date starts today (the channel day)."""
    if mailbox.warmup_start_cap is None:
        return []
    if mailbox.daily_cap is None:
        raise MailboxError("a warm-up needs daily_cap as its ceiling")
    if mailbox.warmup_started_on is not None:
        return []
    mailbox.warmup_started_on = clock_service.now_for(mailbox.channel).date()
    return ["warmup_started_on"]


@transaction.atomic
def _saved(mailbox: Mailbox, fields: list[str] | None, *, explicit_user_default: bool) -> Mailbox:
    """`fields` None = a new row. One default per channel: a new default takes the flag from the old one; the same
    for the default of a user. A mailbox without a user is nobody's default; a user's first mailbox (or one moved to
    a user without a default) becomes the user's default; the user it left gets their oldest other one."""
    previous_user_id = None
    if mailbox.pk:
        previous_user_id = Mailbox.objects.filter(pk=mailbox.pk).values_list("user_id", flat=True).first()
    fields = _user_default_fields(mailbox, fields, previous_user_id, explicit_user_default)
    if mailbox.is_default:
        Mailbox.objects.filter(channel_id=mailbox.channel_id, is_default=True).exclude(pk=mailbox.pk).update(
            is_default=False
        )
    if mailbox.is_user_default:
        Mailbox.objects.filter(channel_id=mailbox.channel_id, user_id=mailbox.user_id, is_user_default=True).exclude(
            pk=mailbox.pk
        ).update(is_user_default=False)
    try:
        with transaction.atomic():
            if fields is None:
                mailbox.save()
            else:
                mailbox.save(update_fields=[*fields, "modified_at"])
    except IntegrityError:
        raise MailboxError("another mailbox of this channel has this address") from None
    if previous_user_id is not None and previous_user_id != mailbox.user_id:
        _promote_user_default(mailbox.channel_id, previous_user_id)
    return mailbox


def _user_default_fields(
    mailbox: Mailbox, fields: list[str] | None, previous_user_id: int | None, explicit: bool
) -> list[str] | None:
    """Decide `is_user_default` of a new mailbox or one that changed user, unless the caller set it; the columns to
    write (None = a new row, every column)."""
    before = mailbox.is_user_default
    if mailbox.user_id is None:
        mailbox.is_user_default = False
    elif (fields is None or mailbox.user_id != previous_user_id) and not explicit:
        others = Mailbox.objects.filter(channel_id=mailbox.channel_id, user_id=mailbox.user_id, is_user_default=True)
        mailbox.is_user_default = not others.exclude(pk=mailbox.pk).exists()
    if fields is not None and mailbox.is_user_default != before and "is_user_default" not in fields:
        fields = [*fields, "is_user_default"]
    return fields


def _promote_user_default(channel_id: int, user_id: int | None) -> None:
    """The user's oldest remaining mailbox becomes the default when none is."""
    if user_id is None:
        return
    mailboxes = Mailbox.objects.filter(channel_id=channel_id, user_id=user_id)
    if not mailboxes.filter(is_user_default=True).exists() and (oldest := mailboxes.order_by("pk").first()):
        Mailbox.objects.filter(pk=oldest.pk).update(is_user_default=True)


def resolve(channel: Channel, user_id: int | None, brand: str = "") -> Mailbox | None:
    """Among sending mailboxes: the user's mailbox of that brand (the user's default first, else the oldest), else
    the user's default, else the user's oldest, else the channel default, else None."""
    sending = Mailbox.objects.filter(channel=channel, send_enabled=True)
    if user_id is not None:
        own = sending.filter(user_id=user_id).order_by("-is_user_default", "pk")
        brand = (brand or "").strip().lower()
        if brand and (branded := own.filter(brand=brand).first()):
            return branded
        if mailbox := own.first():
            return mailbox
    return sending.filter(is_default=True).first()


def assign(thread: Thread, user_id: int | None, brand: str = "") -> None:
    """Before the thread's first message: store the mailbox it sends from. A thread keeps it for good."""
    if thread.mailbox_id is not None or thread.messages.exists():
        return
    mailbox = resolve(thread.channel, user_id, brand)
    if mailbox is not None and Thread.objects.filter(pk=thread.pk, mailbox__isnull=True).update(mailbox=mailbox):
        thread.mailbox = mailbox


def for_message(message: Message) -> Mailbox | None:
    return message.thread.mailbox


def _smtp_ready(mailbox: Mailbox) -> bool:
    return bool(mailbox.smtp_host and mailbox.smtp_password)


def _imap_ready(mailbox: Mailbox) -> bool:
    return bool(mailbox.imap_host and mailbox.imap_password)


def needs_login(mailbox: Mailbox) -> bool:
    smtp_broken = not _smtp_ready(mailbox) or mailbox.smtp_login_failed_at is not None
    return (mailbox.send_enabled and smtp_broken) or (mailbox.receive_enabled and not _imap_ready(mailbox))


def state(mailbox: Mailbox) -> str:
    if mailbox.paused_at is not None:
        return MailboxState.PAUSED
    if needs_login(mailbox):
        return MailboxState.NEEDS_LOGIN
    if not (mailbox.send_enabled or mailbox.receive_enabled):
        return MailboxState.DISABLED
    return MailboxState.ACTIVE


def send_hold(mailbox: Mailbox) -> str | None:
    """Why the mailbox holds its mail (`paused`, `disabled`, `needs_login`); None when it can send."""
    if mailbox.paused_at is not None:
        return MailboxState.PAUSED
    if not mailbox.send_enabled:
        return MailboxState.DISABLED
    if not _smtp_ready(mailbox) or mailbox.smtp_login_failed_at is not None:
        return MailboxState.NEEDS_LOGIN
    return None


def sender(mailbox: Mailbox) -> str:
    return formataddr((mailbox.display_name, mailbox.address))


def smtp_connection(mailbox: Mailbox) -> BaseEmailBackend:
    """The mailbox's SMTP connection through the host's `EMAIL_BACKEND` (the send and the login test alike). Raises
    `MailboxLoginError` instead of logging in with empty credentials."""
    if not _smtp_ready(mailbox):
        raise MailboxLoginError(f"mailbox {mailbox.pk} has no SMTP host or password")
    use_ssl = mailbox.smtp_security == SmtpSecurity.SSL
    return get_connection(
        host=mailbox.smtp_host,
        port=mailbox.smtp_port,
        username=mailbox.smtp_user or mailbox.address,
        password=mailbox.smtp_password,
        use_ssl=use_ssl,
        use_tls=not use_ssl,
        timeout=LOGIN_TIMEOUT_S,
    )


def imap_login(mailbox: Mailbox) -> imaplib.IMAP4:
    """A logged-in IMAP client; the caller logs out. Raises `MailboxLoginError` without host or password."""
    if not _imap_ready(mailbox):
        raise MailboxLoginError(f"mailbox {mailbox.pk} has no IMAP host or password")
    client = imaplib.IMAP4_SSL if mailbox.imap_use_ssl else imaplib.IMAP4
    imap = client(mailbox.imap_host, mailbox.imap_port, timeout=LOGIN_TIMEOUT_S)
    try:
        imap.login(mailbox.imap_user or mailbox.address, mailbox.imap_password)
    except (IMAP_ERROR, OSError):
        imap.shutdown()
        raise
    return imap


def mark_needs_login(mailbox: Mailbox, now: datetime) -> None:
    """SMTP refused the login: the mailbox holds its mail until a new password is saved; one `high` notification
    per mailbox and channel day."""
    Mailbox.objects.filter(pk=mailbox.pk, smtp_login_failed_at__isnull=True).update(smtp_login_failed_at=now)
    mailbox.smtp_login_failed_at = mailbox.smtp_login_failed_at or now
    notify_needs_login(mailbox, now)


def notify_needs_login(mailbox: Mailbox, now: datetime) -> None:
    """One `high` notification per mailbox and channel day; never names a credential."""
    title = f"Mailbox {mailbox.address} needs a new login: its mail waits"
    kind = f"mailbox_login:{mailbox.pk}"
    alert_service.notify_once(mailbox.channel, kind=kind, severity="high", title=title, day=now.date())


def check_login(mailbox: Mailbox) -> dict[str, str]:
    """Log in to SMTP (the connection a send uses) and IMAP: `ok` or a code — `certificate` for a server certificate
    the host does not accept, `unreachable` for a server that does not answer. A working SMTP login clears
    `needs_login`."""
    result = {"smtp": _test_smtp(mailbox), "imap": _test_imap(mailbox)}
    if result["smtp"] == OK and mailbox.smtp_login_failed_at is not None:
        Mailbox.objects.filter(pk=mailbox.pk).update(smtp_login_failed_at=None)
        mailbox.smtp_login_failed_at = None
    return result


def _test_smtp(mailbox: Mailbox) -> str:
    if not mailbox.smtp_host:
        return NOT_CONFIGURED
    if not mailbox.smtp_password:
        return NO_PASSWORD
    connection = smtp_connection(mailbox)
    try:
        connection.open()
    except smtplib.SMTPAuthenticationError:
        return AUTH_FAILED
    except ssl.SSLCertVerificationError:
        return CERTIFICATE
    except (smtplib.SMTPException, OSError):
        return UNREACHABLE
    connection.close()
    return OK


def _test_imap(mailbox: Mailbox) -> str:
    if not mailbox.imap_host:
        return NOT_CONFIGURED
    if not mailbox.imap_password:
        return NO_PASSWORD
    try:
        imap = imap_login(mailbox)
    except IMAP_ERROR:
        return AUTH_FAILED
    except ssl.SSLCertVerificationError:
        return CERTIFICATE
    except OSError:
        return UNREACHABLE
    imap.logout()
    return OK


def effective_cap(mailbox: Mailbox, day: date) -> int | None:
    """Deliveries allowed from the mailbox on `day`; None = only the channel cap.

    With a warm-up: `warmup_start_cap + warmup_step` per calendar day since `warmup_started_on` (weekends count),
    never above `daily_cap`; before the start date the start cap."""
    if mailbox.warmup_start_cap is None or mailbox.warmup_started_on is None:
        return mailbox.daily_cap
    grown = mailbox.warmup_start_cap + mailbox.warmup_step * max((day - mailbox.warmup_started_on).days, 0)
    return min(grown, mailbox.daily_cap) if mailbox.daily_cap is not None else grown
