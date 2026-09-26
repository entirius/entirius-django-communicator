# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Configuration health checks (tag ``entirius_config``), aggregated by django-munin.

Warnings only: a config gap must never stop ``runserver`` or ``migrate``. DB-bound checks read rows only when
the caller passes ``databases`` (``manage.py check --database default``, munin's endpoint) — never at boot — and
only once the table exists: ``migrate`` runs the checks with ``databases`` before it creates anything.
"""

from django.core import checks
from django.db import connections
from django_email.domain import EmailDomain

from django_communicator.enums import ChannelMode

SMTP_CODE = "communicator.smtp"
SMTP_FIX_URL = "https://github.com/entirius/entirius-django-communicator/blob/master/docs/install.md#prerequisites"


class _Subject(dict):
    """Row metadata munin reads as a dict; `manage.py check` prints `str(obj)` as the label, so name the subject."""

    def __str__(self) -> str:
        return self.get("scope") or "settings"


@checks.register("entirius_config", SMTP_CODE)
def sending_channels_have_smtp(app_configs=None, databases=None, **kwargs) -> list[checks.CheckMessage]:
    """A sending channel needs a complete SMTP entry: without one `mail_builder.build` sends nothing (no global fallback)."""
    from django_communicator.models import Channel

    if (
        "default" not in (databases or ())
        or Channel._meta.db_table not in connections["default"].introspection.table_names()
    ):
        return []
    sending = Channel.objects.exclude(mode=ChannelMode.DRY_RUN).values_list("idx", flat=True)
    return [_smtp_missing(idx) for idx in sending if EmailDomain(channel_idx=idx).channel_smtp_connection is None]


def _smtp_missing(idx: str) -> checks.Warning:
    return checks.Warning(
        f"SMTP not configured for channel {idx}",
        hint=f"Add a complete EMAIL_SMTP_CONFIGURATION_CHANNELS['{idx}'] entry; until then no message of this "
        "channel is sent (the global EMAIL_HOST is not a fallback here).",
        id=SMTP_CODE,
        obj=_Subject(scope=idx, state="unconfigured", severity="high", fix_url=SMTP_FIX_URL),
    )
