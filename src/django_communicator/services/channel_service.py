# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_communicator.models import Channel


def get_channel(idx: str) -> Channel:
    """Raises `Channel.DoesNotExist` for an unknown idx."""
    return Channel.objects.get(idx=idx)
