# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""What must hold before an outbound message may be approved or leave: the legal footer.

`communicate()` already refuses a missing footer at creation; this check also covers a template that started to
require one later and a message without a template version (always required), and runs again at delivery.
"""

from django_communicator.enums import FailureCode
from django_communicator.models import Message

LEGAL_FOOTER_MISSING = "the message requires a legal footer and has none"


def problem(message: Message) -> tuple[str, str] | None:
    """(failure code, readable detail) of the first problem, None when the message may go."""
    if not message.legal_footer.strip() and _requires_footer(message):
        return FailureCode.LEGAL_FOOTER, LEGAL_FOOTER_MISSING
    return None


def _requires_footer(message: Message) -> bool:
    version = message.template_version
    return version is None or version.template.requires_legal_footer
