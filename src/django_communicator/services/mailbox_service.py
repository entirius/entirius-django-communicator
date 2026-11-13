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
from datetime import date, datetime
from email.utils import formataddr
from typing import Any

from django.contrib.auth import get_user_model
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from django_communicator.enums import MailboxState, SmtpSecurity
from django_communicator.models import Channel, Mailbox, Message, Thread
from django_communicator.services import alert_service, clock_service
from django_communicator.services.exceptions import ClientError

PASSWORD_FIELDS = ("smtp_password", "imap_password")
EDITABLE_FIELDS = frozenset(
    {
        "address",
        "display_name",
        "cc_address",
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
_RELATION_FIELDS = frozenset({"user_id", "is_default", *PASSWORD_FIELDS})
IMAP_IDENTITY = ("imap_host", "imap_user", "folder")
LOGIN_TIMEOUT_S = 30
IMAP_ERROR = imaplib.IMAP4.error  # bound at import: a test may replace imaplib.IMAP4
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
OK, NOT_CONFIGURED, NO_PASSWORD, AUTH_FAILED, UNREACHABLE = (
    "ok",
    "not_configured",
    "no_password",
    "auth_failed",
    "unreachable",
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
    """Raises `MailboxError` for a taken address or user, a warm-up without a ceiling or an unknown user."""
    mailbox = Mailbox(channel=channel)
    _apply(mailbox, changes)
    return _saved(mailbox, None)


def update_mailbox(mailbox: Mailbox, changes: dict[str, Any]) -> Mailbox:
    """Only the named fields are written; a password column only when `changes` carries a new password."""
    fields = _apply(mailbox, changes)
    return _saved(mailbox, fields)


def check_changes(mailbox: Mailbox, changes: dict[str, Any]) -> None:
    """Raise what `update_mailbox` / `create_mailbox` would raise for `changes`, without writing anything."""
    _apply(copy.copy(mailbox), changes)


def delete_mailbox(mailbox: Mailbox) -> None:
    """Raises `MailboxInUseError` while it is the default or a thread sends from it."""
    if mailbox.is_default:
        raise MailboxInUseError("the default mailbox cannot be deleted; choose another default first")
    if Thread.objects.filter(mailbox=mailbox).exists():
        raise MailboxInUseError("threads send from this mailbox; disable it instead")
    mailbox.delete()


def _apply(mailbox: Mailbox, changes: dict[str, Any]) -> list[str]:
    """Set the changes on the instance; the names of the columns to write."""
    invalid = set(changes) - EDITABLE_FIELDS - _RELATION_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable on a mailbox: {sorted(invalid)}")
    identity = [getattr(mailbox, name) for name in IMAP_IDENTITY]
    fields = sorted(EDITABLE_FIELDS & set(changes))
    for name in fields:
        setattr(mailbox, name, changes[name])
    fields += _set_passwords(mailbox, changes) + _set_relations(mailbox, changes)
    if mailbox.pk and identity != [getattr(mailbox, name) for name in IMAP_IDENTITY]:
        mailbox.last_uid, mailbox.uidvalidity = 0, None  # another mailbox: the cursor starts over
        fields += ["last_uid", "uidvalidity"]
    return [*fields, *_check_warmup(mailbox)]


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
def _saved(mailbox: Mailbox, fields: list[str] | None) -> Mailbox:
    """`fields` None = a new row. One default per channel: a new default takes the flag from the old one."""
    if mailbox.is_default:
        Mailbox.objects.filter(channel_id=mailbox.channel_id, is_default=True).exclude(pk=mailbox.pk).update(
            is_default=False
        )
    try:
        with transaction.atomic():
            if fields is None:
                mailbox.save()
            else:
                mailbox.save(update_fields=[*fields, "modified_at"])
    except IntegrityError:
        raise MailboxError("another mailbox of this channel has this address or this user") from None
    return mailbox


def resolve(channel: Channel, user_id: int | None) -> Mailbox | None:
    """The sending mailbox of that user in the channel, else the channel's sending default, else None."""
    sending = Mailbox.objects.filter(channel=channel, send_enabled=True)
    if user_id is not None and (own := sending.filter(user_id=user_id).first()):
        return own
    return sending.filter(is_default=True).first()


def assign(thread: Thread, user_id: int | None) -> None:
    """Before the thread's first message: store the mailbox it sends from. A thread keeps it for good."""
    if thread.mailbox_id is not None or thread.messages.exists():
        return
    mailbox = resolve(thread.channel, user_id)
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


def smtp_connection(mailbox: Mailbox, backend: str | None = None) -> BaseEmailBackend:
    """The mailbox's SMTP connection (the host's `EMAIL_BACKEND` unless `backend` is given). Raises
    `MailboxLoginError` instead of logging in with empty credentials."""
    if not _smtp_ready(mailbox):
        raise MailboxLoginError(f"mailbox {mailbox.pk} has no SMTP host or password")
    use_ssl = mailbox.smtp_security == SmtpSecurity.SSL
    return get_connection(
        backend,
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
    """Log in to SMTP and IMAP: `ok` or a code. A working SMTP login clears `needs_login`."""
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
    connection = smtp_connection(mailbox, backend=SMTP_BACKEND)
    try:
        connection.open()
    except smtplib.SMTPAuthenticationError:
        return AUTH_FAILED
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
