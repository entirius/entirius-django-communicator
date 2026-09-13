# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.core.management import call_command


def test_openapi_schema_validates(tmp_path):
    call_command("spectacular", "--validate", "--fail-on-warn", "--file", str(tmp_path / "schema.yaml"))
