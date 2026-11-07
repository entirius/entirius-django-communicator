# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Replies through the Gmail API: new inbox mail of every connected account with `inbound_enabled` goes through the
same `inbound_service.ingest` as IMAP mail (thread match by In-Reply-To / References, suppression, opt-out, DSN,
classification), so the result is identical and a second read of a message changes nothing (dedup by Message-ID).

The cursor is the account's Gmail `historyId`: the first run only starts it (replies are read from the moment the
account is connected), later runs read `history.list` (messageAdded to INBOX). An expired history id (Google keeps
about a week) falls back to the newest inbox messages; ingest drops what it already knows.
"""

import logging
from collections import Counter

from django.db import DatabaseError
from django.utils import timezone

from django_communicator import settings as communicator_settings
from django_communicator.models import Channel, MailAccount
from django_communicator.services import (
    alert_service,
    clock_service,
    gmail_client,
    inbound_service,
    mail_account_service,
)

logger = logging.getLogger(__name__)

INGESTED, SKIPPED, QUARANTINED = "ingested", "skipped", "quarantined"


def poll_all(channel: Channel | None = None) -> Counter:
    """Counts over the pollable accounts (of `channel` when given). One account failing never stops the others."""
    counts = Counter({INGESTED: 0, SKIPPED: 0, QUARANTINED: 0})
    for account in mail_account_service.pollable_accounts():
        if channel is not None and account.channel_id != channel.pk:
            continue
        try:
            counts.update(poll_account(account))
        except gmail_client.GmailError as error:
            _alert(account, error)
    return counts


def _alert(account: MailAccount, error: gmail_client.GmailError) -> None:
    logger.warning("communicator gmail poll of account %s failed: %s", account.pk, error.code)
    if isinstance(error, gmail_client.GmailAuthError):
        return  # the account is needs_reauth now and was announced
    channel = account.channel
    alert_service.notify_once(
        channel,
        kind=f"gmail_poll_{account.pk}",
        severity="medium",
        title=f"Reading the inbox of {account.email} failed",
        day=clock_service.now_for(channel).date(),
    )


def poll_account(account: MailAccount) -> Counter:
    """Raises `gmail_client.GmailError` (auth problems also set the account state)."""
    counts = Counter()
    if not account.history_id:
        mail_account_service.start_cursor(account, mail_account_service.access_token(account))
        _stamp(account, account.history_id)
        return counts
    entries, newest = _new_messages(account)
    batch = communicator_settings.COMMUNICATOR_GMAIL_POLL_BATCH
    cursor = account.history_id
    for record, message_id in entries[:batch]:
        counts[_process(account, message_id)] += 1
        cursor = record
    if len(entries) <= batch:
        cursor = newest
    _stamp(account, cursor)
    return counts


def _new_messages(account: MailAccount) -> tuple[list[tuple[str, str]], str]:
    try:
        return mail_account_service.with_token(account, lambda token: gmail_client.history(token, account.history_id))
    except gmail_client.GmailNotFound:
        logger.warning("communicator mail account %s: history id expired, reading the newest inbox mail", account.pk)
        limit = communicator_settings.COMMUNICATOR_GMAIL_POLL_BATCH
        ids = mail_account_service.with_token(
            account, lambda token: gmail_client.list_messages(token, label="INBOX", limit=limit)
        )
        profile = mail_account_service.with_token(account, gmail_client.profile)
        return [(account.history_id, message_id) for message_id in reversed(ids)], str(profile["historyId"])


def _process(account: MailAccount, message_id: str) -> str:
    try:
        raw = mail_account_service.with_token(account, lambda token: gmail_client.get_raw(token, message_id))
    except gmail_client.GmailNotFound:
        return SKIPPED  # deleted since it arrived
    if len(raw) > communicator_settings.COMMUNICATOR_INBOUND_MAX_BYTES:
        logger.warning("communicator mail account %s: message %s is oversized, dropped", account.pk, message_id)
        return QUARANTINED
    try:
        reply = inbound_service.ingest(account.channel, raw)
    except DatabaseError:
        raise
    except Exception as error:  # noqa: BLE001 - a mail the parser cannot read must not block the inbox
        logger.warning(
            "communicator mail account %s: message %s unreadable: %s", account.pk, message_id, type(error).__name__
        )
        return QUARANTINED
    return INGESTED if reply is not None else SKIPPED


def _stamp(account: MailAccount, history_id: str) -> None:
    now = timezone.now()
    MailAccount.objects.filter(pk=account.pk).update(history_id=history_id, last_polled_at=now, last_ok_at=now)
    account.history_id, account.last_polled_at = history_id, now
