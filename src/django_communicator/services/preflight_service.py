# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""What must hold before an outbound message may be approved or leave (outbound Q3, Q6): a usable sender route and,
for a template that requires one, the legal footer. `communicate()` already refuses a missing footer at creation;
this check also covers a template that started to require one later, and runs again at delivery."""

import logging

from django.utils.module_loading import import_string

from django_communicator import settings as communicator_settings
from django_communicator.enums import FailureCode
from django_communicator.models import Message
from django_communicator.services import sender_route_service

logger = logging.getLogger(__name__)

LEGAL_FOOTER_MISSING = "the template requires a legal footer and the message has none"


def address_problem(email: str) -> str | None:
    """Reason code from the host's `COMMUNICATOR_PRESEND_CHECK` callable, None when the address may be mailed or no
    check is configured. The check is advisory infrastructure: an import error or exception never blocks a send."""
    path = communicator_settings.COMMUNICATOR_PRESEND_CHECK
    if not path:
        return None
    try:
        reason = import_string(path)(email)
    except Exception:
        logger.exception("communicator pre-send check %s failed, sending allowed", path)
        return None
    return str(reason) if reason else None


def problem(message: Message, routes: dict | None = None) -> tuple[str, str] | None:
    """(failure code, readable detail) of the first problem, None when the message may go. `routes` is the optional
    preloaded route map of `sender_route_service.route_for`."""
    version = message.template_version
    if version is not None and version.template.requires_legal_footer and not message.legal_footer.strip():
        return FailureCode.LEGAL_FOOTER, LEGAL_FOOTER_MISSING
    try:
        sender_route_service.route_for(message, routes)
    except sender_route_service.SenderRouteError as error:
        return FailureCode.SENDER_ROUTE, error.client_message
    return None
