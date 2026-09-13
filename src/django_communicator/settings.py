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

# Live sends need ENVIRONMENT == "production" on top of the channel flag (enforced by the sending plan).
COMMUNICATOR_LIVE_REQUIRES_PRODUCTION = getattr(settings, "COMMUNICATOR_LIVE_REQUIRES_PRODUCTION", True)

# Sending (beat `send_due` every COMMUNICATOR_SEND_INTERVAL_MIN minutes — also the unit of the spread formula).
COMMUNICATOR_SEND_INTERVAL_MIN = getattr(settings, "COMMUNICATOR_SEND_INTERVAL_MIN", 5)
COMMUNICATOR_SMTP_MAX_ATTEMPTS = getattr(settings, "COMMUNICATOR_SMTP_MAX_ATTEMPTS", 3)
COMMUNICATOR_SANDBOX_SUBJECT_PREFIX = getattr(settings, "COMMUNICATOR_SANDBOX_SUBJECT_PREFIX", "[SANDBOX] ")
COMMUNICATOR_REDIS_URL = getattr(settings, "COMMUNICATOR_REDIS_URL", getattr(settings, "REDIS_URL", ""))
COMMUNICATOR_CLOCK_CACHE_KEY = "communicator:clock:{channel_idx}"
COMMUNICATOR_SENT_COUNTER_KEY = "communicator:sent:{channel_idx}:{day}"
# Workers refuse to start without the celery-once backend (two beats must never send twice).
COMMUNICATOR_REQUIRE_ONCE_BACKEND = getattr(settings, "COMMUNICATOR_REQUIRE_ONCE_BACKEND", True)
