# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Throttle of the public unsubscribe endpoint.

``DEFAULT_THROTTLE_RATES["communicator_unsubscribe"]`` overrides the rate; ``fallback_rate`` is the safety net when
the scope is unset or malformed. The fallback is not a class ``rate``: DRF skips ``get_rate()`` when ``rate`` is
set, which would silently ignore the settings override.
"""

from django.core.exceptions import ImproperlyConfigured
from rest_framework.throttling import AnonRateThrottle


class UnsubscribeThrottle(AnonRateThrottle):
    scope = "communicator_unsubscribe"
    fallback_rate = "20/hour"

    def get_rate(self) -> str:
        """Never ``None`` and never malformed: either would switch throttling off."""
        try:
            rate = super().get_rate()
        except ImproperlyConfigured:
            return self.fallback_rate
        if not rate or "/" not in rate:
            return self.fallback_rate
        return rate
