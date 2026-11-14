# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""What must hold before an outbound message may be approved or leave: the legal footer, a usable mailbox and a
recipient who may be mailed.

`communicate()` already refuses a missing footer at creation; this check also covers a template that started to
require one later and a message without a template version (always required), and runs again at delivery. A
thread's mailbox that is paused, disabled or needs a login refuses the approval; at delivery such a mailbox holds
the mail first (`delivery_service`), so the mailbox check fails a message only when its state changed in between.
The recipient is refused when `can_mail` refuses the address (at delivery that ends the message `suppressed`
first) or when the owner of the thread's subject answers `recipient_check` with a sentence.
"""

from django_communicator.enums import FailureCode
from django_communicator.models import Message
from django_communicator.services import mailbox_service, suppression_service
from django_communicator.signals import recipient_check

LEGAL_FOOTER_MISSING = "the message requires a legal footer and has none"
MAILBOX_UNUSABLE = "the mailbox {address} cannot send ({state})"
ADDRESS_REFUSALS = {
    suppression_service.SUPPRESSED: "the recipient opted out or is on the do-not-contact list",
    suppression_service.ADDRESS_INVALID: "the recipient's address is not valid",
}


def problem(message: Message) -> tuple[str, str] | None:
    """(failure code, readable detail) of the first problem, None when the message may go."""
    if not message.legal_footer.strip() and _requires_footer(message):
        return FailureCode.LEGAL_FOOTER, LEGAL_FOOTER_MISSING
    mailbox = message.thread.mailbox
    if mailbox is not None and (hold := mailbox_service.send_hold(mailbox)):
        return FailureCode.MAILBOX, MAILBOX_UNUSABLE.format(address=mailbox.address, state=hold)
    if refusal := recipient_refusal(message):
        return FailureCode.RECIPIENT, refusal
    return None


def recipient_refusal(message: Message) -> str | None:
    """A sentence when the recipient may not be mailed: the address check, then the owner of the subject."""
    thread = message.thread
    if code := suppression_service.can_mail(thread.channel, thread.recipient_email):
        return ADDRESS_REFUSALS[code]
    answers = recipient_check.send(sender=Message, message=message)
    return next((answer for _, answer in answers if answer), None)


def _requires_footer(message: Message) -> bool:
    version = message.template_version
    return version is None or version.template.requires_legal_footer
