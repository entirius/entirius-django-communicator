# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Module settings — host overrides via Django settings of the same name."""

from django.conf import settings

QUEUE_DEFAULT = getattr(settings, "COMMUNICATOR_QUEUE_DEFAULT", "communicator_default")
QUEUE_SEND = getattr(settings, "COMMUNICATOR_QUEUE_SEND", "communicator_send")
QUEUE_INBOUND = getattr(settings, "COMMUNICATOR_QUEUE_INBOUND", "communicator_inbound")

# Automated rewrite loops stop after this many rewrites of one draft chain; human rewrites are unlimited.
COMMUNICATOR_AUTOMATED_REWRITE_LIMIT = getattr(settings, "COMMUNICATOR_AUTOMATED_REWRITE_LIMIT", 3)

# Sending (beat `send_due` every COMMUNICATOR_SEND_INTERVAL_MIN minutes — also the unit of the spread formula).
COMMUNICATOR_SEND_INTERVAL_MIN = getattr(settings, "COMMUNICATOR_SEND_INTERVAL_MIN", 5)
COMMUNICATOR_SMTP_MAX_ATTEMPTS = getattr(settings, "COMMUNICATOR_SMTP_MAX_ATTEMPTS", 3)
# A `sending` claim older than this is never re-sent: it ends `failed/send_outcome_unknown` for a human to check.
COMMUNICATOR_SENDING_STALE_MINUTES = getattr(settings, "COMMUNICATOR_SENDING_STALE_MINUTES", 30)
COMMUNICATOR_SANDBOX_SUBJECT_PREFIX = getattr(settings, "COMMUNICATOR_SANDBOX_SUBJECT_PREFIX", "[SANDBOX] ")
COMMUNICATOR_REDIS_URL = getattr(settings, "COMMUNICATOR_REDIS_URL", getattr(settings, "REDIS_URL", ""))
COMMUNICATOR_CLOCK_CACHE_KEY = "communicator:clock:{channel_idx}"
COMMUNICATOR_SENT_COUNTER_KEY = "communicator:sent:{channel_idx}:{day}"
# Workers refuse to start without the celery-once backend (two beats must never send twice).
COMMUNICATOR_REQUIRE_ONCE_BACKEND = getattr(settings, "COMMUNICATOR_REQUIRE_ONCE_BACKEND", True)

# Inbound (beat `poll_inbox` every 5 minutes): messages fetched per mailbox per run, soft-bounce retry delay.
COMMUNICATOR_INBOUND_BATCH = getattr(settings, "COMMUNICATOR_INBOUND_BATCH", 50)
COMMUNICATOR_SOFT_BOUNCE_RETRY_H = getattr(settings, "COMMUNICATOR_SOFT_BOUNCE_RETRY_H", 24)
# Mail above this RFC822.SIZE is quarantined unfetched; stored reply bodies are cut to this many characters.
COMMUNICATOR_INBOUND_MAX_BYTES = getattr(settings, "COMMUNICATOR_INBOUND_MAX_BYTES", 10 * 1024 * 1024)
COMMUNICATOR_INBOUND_BODY_MAX_CHARS = getattr(settings, "COMMUNICATOR_INBOUND_BODY_MAX_CHARS", 100_000)
# Lower-case phrases matched case-insensitively on the first 2 000 characters of the reply above the quote; the
# recipient's language list, all languages when the thread has no language with a list.
COMMUNICATOR_OPTOUT_PHRASES = getattr(
    settings,
    "COMMUNICATOR_OPTOUT_PHRASES",
    {
        "pl": ["nie chcę", "proszę nie pisać", "wypisz", "usuń mnie", "rezygnuję"],
        "en": ["unsubscribe", "stop emailing", "remove me", "do not contact", "opt out"],
    },
)
COMMUNICATOR_AUTOREPLY_SUBJECT_PATTERNS = getattr(
    settings,
    "COMMUNICATOR_AUTOREPLY_SUBJECT_PATTERNS",
    ["out of office", "auto-reply", "automatic reply", "autoreply", "poza biurem", "nieobecn"],
)
