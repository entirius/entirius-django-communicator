# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Module settings — host overrides via Django settings of the same name."""

import os

from django.conf import settings

QUEUE_DEFAULT = getattr(settings, "COMMUNICATOR_QUEUE_DEFAULT", "communicator_default")
QUEUE_SEND = getattr(settings, "COMMUNICATOR_QUEUE_SEND", "communicator_send")
QUEUE_INBOUND = getattr(settings, "COMMUNICATOR_QUEUE_INBOUND", "communicator_inbound")

# Automated rewrite loops stop after this many rewrites of one draft chain; human rewrites are unlimited.
COMMUNICATOR_AUTOMATED_REWRITE_LIMIT = getattr(settings, "COMMUNICATOR_AUTOMATED_REWRITE_LIMIT", 3)
# Beat `retry_failed_drafts` retries an AI draft that failed transiently at most this many times.
COMMUNICATOR_DRAFT_RETRY_LIMIT = getattr(settings, "COMMUNICATOR_DRAFT_RETRY_LIMIT", 3)

# Sending (beat `send_due` every COMMUNICATOR_SEND_INTERVAL_MIN minutes — also the unit of the spread formula).
COMMUNICATOR_SEND_INTERVAL_MIN = getattr(settings, "COMMUNICATOR_SEND_INTERVAL_MIN", 5)
COMMUNICATOR_SMTP_MAX_ATTEMPTS = getattr(settings, "COMMUNICATOR_SMTP_MAX_ATTEMPTS", 3)
# A `sending` claim older than this is never re-sent: it ends `failed/send_outcome_unknown` for a human to check.
COMMUNICATOR_SENDING_STALE_MINUTES = getattr(settings, "COMMUNICATOR_SENDING_STALE_MINUTES", 30)
COMMUNICATOR_SANDBOX_SUBJECT_PREFIX = getattr(settings, "COMMUNICATOR_SANDBOX_SUBJECT_PREFIX", "[SANDBOX] ")
COMMUNICATOR_REDIS_URL = getattr(settings, "COMMUNICATOR_REDIS_URL", getattr(settings, "REDIS_URL", ""))
COMMUNICATOR_CLOCK_CACHE_KEY = "communicator:clock:{channel_idx}"
COMMUNICATOR_SENT_COUNTER_KEY = "communicator:sent:{channel_idx}:{day}"
COMMUNICATOR_MAILBOX_COUNTER_KEY = "communicator:mailbox:{mailbox}:{day}"
# Host of the tokens that replace anonymised addresses (same setting as django_leads): always suppressed.
LEADS_ANONYMISED_DOMAIN = getattr(settings, "LEADS_ANONYMISED_DOMAIN", "anonymised.invalid")
# Workers refuse to start without the celery-once backend (two beats must never send twice).
COMMUNICATOR_REQUIRE_ONCE_BACKEND = getattr(settings, "COMMUNICATOR_REQUIRE_ONCE_BACKEND", True)

# Inbound (beat `poll_inbox` every 5 minutes): messages fetched per mailbox per run, soft-bounce retry delay.
COMMUNICATOR_INBOUND_BATCH = getattr(settings, "COMMUNICATOR_INBOUND_BATCH", 50)
# Q30: a spam complaint (ARF report) suppresses the recipient on a live channel; off = the report is ignored as before.
COMMUNICATOR_COMPLAINT_FEEDBACK = getattr(settings, "COMMUNICATOR_COMPLAINT_FEEDBACK", False)
# Q30: sends of one message (the first plus soft-bounce retries) before a further soft bounce fails it; 2 = one retry.
COMMUNICATOR_SOFT_BOUNCE_MAX_ATTEMPTS = getattr(settings, "COMMUNICATOR_SOFT_BOUNCE_MAX_ATTEMPTS", 2)
COMMUNICATOR_SOFT_BOUNCE_RETRY_H = getattr(settings, "COMMUNICATOR_SOFT_BOUNCE_RETRY_H", 24)
# Mail above this RFC822.SIZE is quarantined unfetched; stored reply bodies are cut to this many characters.
COMMUNICATOR_INBOUND_MAX_BYTES = getattr(settings, "COMMUNICATOR_INBOUND_MAX_BYTES", 10 * 1024 * 1024)
COMMUNICATOR_INBOUND_BODY_MAX_CHARS = getattr(settings, "COMMUNICATOR_INBOUND_BODY_MAX_CHARS", 100_000)
# Reply intent classification (T4) over the AI toolbox. Off until a toolbox model id is set (and while
# AI_TOOLBOX_ENABLED is False): replies then keep the phrase detection below and a person reads every one.
COMMUNICATOR_REPLY_INTENT_MODEL = getattr(settings, "COMMUNICATOR_REPLY_INTENT_MODEL", "")
COMMUNICATOR_REPLY_INTENT_MIN_CONFIDENCE = getattr(settings, "COMMUNICATOR_REPLY_INTENT_MIN_CONFIDENCE", 0.7)
COMMUNICATOR_REPLY_INTENT_MAX_CHARS = getattr(settings, "COMMUNICATOR_REPLY_INTENT_MAX_CHARS", 3000)
# Lower-case phrases matched case-insensitively on the first 2 000 characters of the reply above the quote; the
# recipient's language list, all languages when the thread has no language with a list.
COMMUNICATOR_OPTOUT_PHRASES = getattr(
    settings,
    "COMMUNICATOR_OPTOUT_PHRASES",
    {
        # Matched on text folded to lower case, without diacritics and with punctuation as one space, so a phrase is
        # also a stem: "nie kontaktowa" covers "nie kontaktowanie się" and "nie kontaktować".
        "pl": [
            "nie chcę",
            "nie chcemy",
            "proszę nie pisać",
            "proszę nie kontaktować",
            "nie kontaktowa",
            "nie kontaktuj",
            "nie piszcie",
            "nie wysyłać",
            "nie wysyłaj",
            "wypisz",
            "wypis",
            "usuń mnie",
            "usunąć mnie",
            "usunięcie mnie",
            "usuń nas",
            "usunąć nas",
            "usunięcie nas",
            "z listy mailingowej",
            "z listy kontaktów",
            "rezygnuj",
            "rezygnacj",
            "zaprzestań",
            "zaprzestanie",
            "nie jestem zainteresowan",
            "nie jesteśmy zainteresowan",
        ],
        "en": [
            "unsubscribe",
            "stop emailing",
            "stop sending",
            "remove me",
            "take me off",
            "do not contact",
            "don't contact",
            "do not email",
            "don't email",
            "opt out",
            "opt-out",
            "no longer wish",
            "not interested",
        ],
    },
)
COMMUNICATOR_AUTOREPLY_SUBJECT_PATTERNS = getattr(
    settings,
    "COMMUNICATOR_AUTOREPLY_SUBJECT_PATTERNS",
    ["out of office", "auto-reply", "automatic reply", "autoreply", "poza biurem", "nieobecn"],
)

# Approval inbox (outbound Q4): most drafts one bulk approve / reject call may touch.
COMMUNICATOR_BULK_APPROVAL_MAX = getattr(settings, "COMMUNICATOR_BULK_APPROVAL_MAX", 100)
# One-click unsubscribe (outbound Q6). Public base URL of the host that mounts `django_communicator.urls`, without a
# trailing slash (e.g. "https://api.example.com"). Empty = no List-Unsubscribe headers and no link in the mail.
COMMUNICATOR_UNSUBSCRIBE_BASE_URL = getattr(settings, "COMMUNICATOR_UNSUBSCRIBE_BASE_URL", "")
# Pre-send address check (outbound glue). Dotted path of a callable `(email: str) -> str | None` that returns a reason
# code when the address must not be mailed now (None = fine), e.g. "django_leads.services.verification_service.presend_check".
# Empty = no check. A path that cannot be imported or a callable that raises never blocks a send (logged).
COMMUNICATOR_PRESEND_CHECK = getattr(settings, "COMMUNICATOR_PRESEND_CHECK", "")


def _env(name: str, default: str = "") -> str:
    """A Django setting of this name, else the environment variable, else the default (secrets are never in the repo)."""
    return getattr(settings, name, os.environ.get(name, default))


# Gmail API (outbound): OAuth client of the Google Cloud project. They come from the environment of the host (never from
# the repository); empty = "gmail_not_configured" on connect. The URLs default to Google; tests and local runs point
# them at the stub in tests/gmail_stub.py, which is the only way Sandbox / test-send may call the API at all.
COMMUNICATOR_GMAIL_CLIENT_ID = _env("COMMUNICATOR_GMAIL_CLIENT_ID", "")
COMMUNICATOR_GMAIL_CLIENT_SECRET = _env("COMMUNICATOR_GMAIL_CLIENT_SECRET", "")
COMMUNICATOR_GMAIL_REDIRECT_URI = _env("COMMUNICATOR_GMAIL_REDIRECT_URI", "")
# Where the callback page sends the browser afterwards (e.g. the CMS settings screen); empty = a plain result page.
COMMUNICATOR_GMAIL_RETURN_URL = _env("COMMUNICATOR_GMAIL_RETURN_URL", "")
COMMUNICATOR_GMAIL_API_BASE = _env("COMMUNICATOR_GMAIL_API_BASE", "https://gmail.googleapis.com")
COMMUNICATOR_GMAIL_AUTH_URL = _env("COMMUNICATOR_GMAIL_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")
COMMUNICATOR_GMAIL_TOKEN_URL = _env("COMMUNICATOR_GMAIL_TOKEN_URL", "https://oauth2.googleapis.com/token")
COMMUNICATOR_GMAIL_REVOKE_URL = _env("COMMUNICATOR_GMAIL_REVOKE_URL", "https://oauth2.googleapis.com/revoke")
COMMUNICATOR_GMAIL_TIMEOUT_S = getattr(settings, "COMMUNICATOR_GMAIL_TIMEOUT_S", 20)
# Inbox messages read per account per poll run; a longer backlog is read by the next runs.
COMMUNICATOR_GMAIL_POLL_BATCH = getattr(settings, "COMMUNICATOR_GMAIL_POLL_BATCH", 50)
