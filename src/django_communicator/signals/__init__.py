# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Signals consumers connect to (all sent on commit, except `draft_retry_requested`).

- `message_approved(sender=Message, message=...)` — a reviewer accepted the message.
- `company_skipped(sender=Message, subject_ref=..., message=...)` — the reviewer skipped the whole subject.
- `message_sent(sender=Message, message=...)`, `sequence_finished(sender=Thread, thread=...)`.
- `reply_received(sender=Thread, thread=..., reply=...)` — a human reply (or a dismissed opt-out suspicion).
- `optout_confirmed(sender=Reply, subject_ref=..., email=..., channel_idx=...)` — a human confirmed an opt-out.
- `draft_retry_requested(sender=Message, message=...)` — sent synchronously before a failed draft is retried; the
  owner of the thread's `subject_ref` returns a reason code to block the retry, None to allow it.
"""

from django.dispatch import Signal

message_approved = Signal()
company_skipped = Signal()
message_sent = Signal()
sequence_finished = Signal()
reply_received = Signal()
optout_confirmed = Signal()
draft_retry_requested = Signal()
