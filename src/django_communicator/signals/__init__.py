# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Signals consumers connect to. Emitted here: `message_approved`, `company_skipped`; the rest from plans 06/07.

- `message_approved(sender=Message, message=...)` — a reviewer accepted the message.
- `company_skipped(sender=Message, subject_ref=..., message=...)` — the reviewer skipped the whole subject.
- `message_sent(sender=Message, message=...)`, `sequence_finished(sender=..., thread=...)`,
  `reply_received(sender=Message, message=...)`, `optout_confirmed(sender=..., thread=...)`.
"""

from django.dispatch import Signal

message_approved = Signal()
company_skipped = Signal()
message_sent = Signal()
sequence_finished = Signal()
reply_received = Signal()
optout_confirmed = Signal()
