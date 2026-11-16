# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Configuration health checks (tag ``entirius_config``), aggregated by django-munin.

Warnings only: a config gap must never stop ``runserver`` or ``migrate``. DB-bound checks read rows only when
the caller passes ``databases`` (``manage.py check --database default``, munin's endpoint) — never at boot — and
only once every migration of this app is applied: ``migrate`` runs the checks with ``databases`` before it applies
anything, so on a database upgraded from an older release the tables and columns the models name may not exist yet.
"""

from django.core import checks
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django_email.domain import EmailDomain

from django_communicator.enums import ChannelMode

APP_LABEL = "django_communicator"
SMTP_CODE = "communicator.smtp"
SMTP_FIX_URL = "https://github.com/entirius/entirius-django-communicator/blob/master/docs/install.md#prerequisites"


def _channels_readable(databases) -> bool:
    """Only with `databases` (never at boot) and on a schema at the models: `migrate` checks before it migrates, and
    a database from an older release lacks tables and columns the queries below join."""
    if "default" not in (databases or ()):
        return False
    executor = MigrationExecutor(connections["default"])
    return not executor.migration_plan(executor.loader.graph.leaf_nodes(APP_LABEL))


class _Subject(dict):
    """Row metadata munin reads as a dict; `manage.py check` prints `str(obj)` as the label, so name the subject."""

    def __str__(self) -> str:
        return self.get("scope") or "settings"


@checks.register("entirius_config", SMTP_CODE)
def sending_channels_have_smtp(app_configs=None, databases=None, **kwargs) -> list[checks.CheckMessage]:
    """A sending channel without a sending default mailbox needs a complete SMTP entry: without one `mail_builder.build`
    sends nothing for threads without a mailbox (no global fallback)."""
    from django_communicator.models import Channel

    if not _channels_readable(databases):
        return []
    channels = Channel.objects.exclude(mode=ChannelMode.DRY_RUN)
    with_default = channels.filter(mailboxes__is_default=True, mailboxes__send_enabled=True)
    sending = channels.exclude(pk__in=with_default.values("pk")).values_list("idx", flat=True)
    return [_smtp_missing(idx) for idx in sending if EmailDomain(channel_idx=idx).channel_smtp_connection is None]


def _smtp_missing(idx: str) -> checks.Warning:
    return checks.Warning(
        f"SMTP not configured for channel {idx}",
        hint=f"Add a complete EMAIL_SMTP_CONFIGURATION_CHANNELS['{idx}'] entry; until then no message of this "
        "channel without a mailbox is sent (the global EMAIL_HOST is not a fallback here); or add a sending default "
        "mailbox to the channel.",
        id=SMTP_CODE,
        obj=_Subject(scope=idx, state="unconfigured", severity="high", fix_url=SMTP_FIX_URL),
    )


RESERVED_IDX_CODE = "communicator.reserved_idx"


@checks.register("entirius_config", RESERVED_IDX_CODE)
def no_channel_shadows_the_approvals_route(app_configs=None, databases=None, **kwargs) -> list[checks.CheckMessage]:
    """A channel idx equal to the approvals segment would answer on the cross-channel inbox, never on its own API."""
    from django_communicator.models import Channel
    from django_communicator.urls import APPROVALS

    if not _channels_readable(databases):
        return []
    if not Channel.objects.filter(idx=APPROVALS).exists():
        return []
    message = f"Channel idx '{APPROVALS}' is reserved by the approval inbox route"
    hint = "Rename the channel: its channel-scoped admin API is unreachable under this idx."
    return [checks.Error(message, hint=hint, id=RESERVED_IDX_CODE, obj=_Subject(scope=APPROVALS, severity="high"))]
