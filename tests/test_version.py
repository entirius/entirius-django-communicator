# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""munin reports `__version__`; it must be the version the package is released as."""

import tomllib
from pathlib import Path

import django_communicator

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_matches_pyproject():
    assert django_communicator.__version__ == tomllib.loads(PYPROJECT.read_text())["project"]["version"]
