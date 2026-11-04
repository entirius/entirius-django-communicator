# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The base of every refusal whose text this module writes for an API client."""


class ClientError(Exception):
    """A refusal whose message this module wrote for the API client.

    A view answers with ``exc.client_message``, never with ``str(exc)``: only these classes carry text meant for a
    response, so an exception from a lower layer cannot reach a client by being caught too broadly.
    """

    def __init__(self, client_message: str) -> None:
        super().__init__(client_message)
        self.client_message = client_message
