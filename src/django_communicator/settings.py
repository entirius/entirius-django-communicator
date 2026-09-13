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
