# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Sender routes (outbound Q3): CRUD per channel, resolution of a thread's route key, the per-mailbox daily cap
with its warm-up schedule (Q8).

The caller (leads) stores only a key string on the thread; the key is resolved here when a draft is approved and
when the mail is built. No key = the channel's default route; no default either = the channel's own From, no CC.
"""

from datetime import date

from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from django_communicator.enums import MailTransport
from django_communicator.models import Channel, Message, SenderRoute
from django_communicator.services.exceptions import ClientError

EDITABLE_FIELDS = (
    "name",
    "from_email",
    "cc_email",
    "is_active",
    "daily_cap",
    "warmup_start_cap",
    "warmup_step",
    "warmup_started_on",
    "transport",
    "mail_account",
)


class SenderRouteError(ClientError):
    """The route a message names cannot be used (unknown or inactive) — the message is not sent."""


class DuplicateRouteError(ClientError):
    pass


class RouteInUseError(ClientError):
    """The route is the channel default; choose another default first."""


class RouteConfigError(ClientError):
    """The warm-up asks for a ceiling the route does not have."""


def list_routes(channel: Channel) -> QuerySet[SenderRoute]:
    return SenderRoute.objects.filter(channel=channel).order_by("key")


def get_route(channel: Channel, key: str) -> SenderRoute:
    """Raises `SenderRoute.DoesNotExist`."""
    return SenderRoute.objects.get(channel=channel, key=key)


def _check(fields: dict, channel: Channel) -> None:
    if fields.get("warmup_start_cap") is not None and fields.get("daily_cap") is None:
        raise RouteConfigError("a warm-up needs daily_cap as its ceiling")
    if fields.get("transport", MailTransport.SMTP) == MailTransport.GMAIL_API:
        account = fields.get("mail_account")
        if account is None or account.channel_id != channel.pk:
            raise RouteConfigError("transport gmail_api needs a mail account of this channel")
        if account.email.lower() != fields["from_email"].strip().lower():
            raise RouteConfigError("the mail account address must be the From address of the route")
    elif fields.get("mail_account") is not None:
        raise RouteConfigError("a mail account is only used by transport gmail_api")


def _start_date(fields: dict, today: date | None) -> dict:
    """Enabling a warm-up without a start date starts it today (the channel's day)."""
    if fields.get("warmup_start_cap") is not None and fields.get("warmup_started_on") is None and today:
        return {**fields, "warmup_started_on": today}
    return fields


def create_route(channel: Channel, *, key: str, today: date | None = None, **fields) -> SenderRoute:
    """Raises `DuplicateRouteError` for an existing key and `RouteConfigError` for a warm-up without a ceiling."""
    _check(fields, channel)
    try:
        with transaction.atomic():
            return SenderRoute.objects.create(channel=channel, key=key, **_start_date(fields, today))
    except IntegrityError:
        raise DuplicateRouteError(f"sender route {key} already exists") from None


@transaction.atomic
def replace_route(route: SenderRoute, *, today: date | None = None, **fields) -> SenderRoute:
    fields = {"transport": MailTransport.SMTP, "mail_account": None, **fields}
    _check(fields, route.channel)
    fields = _start_date(fields, today)
    if fields.get("warmup_start_cap") is None:
        fields["warmup_started_on"] = None
    for name in EDITABLE_FIELDS:
        setattr(route, name, fields[name])
    route.save(update_fields=[*EDITABLE_FIELDS, "modified_at"])
    return route


def delete_route(route: SenderRoute) -> None:
    """Raises `RouteInUseError` while the channel default points at it. Threads keep the key; an unknown key makes
    their messages fail with a readable error, never a wrong From."""
    if route.channel.default_sender_route == route.key:
        raise RouteInUseError(f"sender route {route.key} is the channel default")
    route.delete()


def set_default(channel: Channel, key: str) -> Channel:
    """`key` blank clears the default. Raises `SenderRouteError` for a key that is not an active route."""
    if key and not SenderRoute.objects.filter(channel=channel, key=key, is_active=True).exists():
        raise SenderRouteError(f"sender route {key} is unknown or inactive")
    channel.default_sender_route = key
    channel.save(update_fields=["default_sender_route", "modified_at"])
    return channel


def route_key(message: Message) -> str:
    return message.thread.sender_route or message.thread.channel.default_sender_route


def route_for(message: Message, preloaded: dict[tuple[int, str], SenderRoute] | None = None) -> SenderRoute | None:
    """The route the mail leaves by; None when neither the thread nor the channel names one.

    `preloaded` ({(channel id, key): route}) saves the query when many messages are checked together.
    Raises `SenderRouteError` for a named route that does not exist or is inactive."""
    key = route_key(message)
    if not key:
        return None
    if preloaded is not None:
        found = preloaded.get((message.thread.channel_id, key))
        route = found if found is not None and found.is_active else None
    else:
        route = (
            SenderRoute.objects.select_related("mail_account")
            .filter(channel_id=message.thread.channel_id, key=key, is_active=True)
            .first()
        )
    if route is None:
        raise SenderRouteError(f"sender route {key} is unknown or inactive")
    return route


def effective_cap(route: SenderRoute, day: date) -> int | None:
    """Deliveries allowed from the route's mailbox on `day`; None = no mailbox cap.

    With a warm-up the cap is `warmup_start_cap + warmup_step` per calendar day since `warmup_started_on`
    (weekends count), never above `daily_cap`; before the start date it is the start cap."""
    if route.warmup_start_cap is None or route.warmup_started_on is None:
        return route.daily_cap
    grown = route.warmup_start_cap + route.warmup_step * max((day - route.warmup_started_on).days, 0)
    return grown if route.daily_cap is None else min(grown, route.daily_cap)
