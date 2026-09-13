# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Placeholder rendering: `{name}` from the given values; every missing name is reported, extra values ignored."""

from string import Formatter
from typing import Any


class RenderError(Exception):
    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"missing placeholders: {', '.join(missing)}")


def render(text: str, values: dict[str, Any]) -> str:
    """Format `text` with `values`; raises `RenderError` listing absent placeholders (or a malformed template)."""
    try:
        names = [field for _, field, _, _ in Formatter().parse(text) if field is not None]
    except ValueError:
        raise RenderError(["<malformed template>"]) from None
    missing = sorted({_root(name) for name in names} - values.keys())
    if missing:
        raise RenderError(missing)
    try:
        return text.format_map(values)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        raise RenderError(["<malformed placeholder>"]) from None


def _root(field: str) -> str:
    """`{company.name}` and `{hooks[0]}` need `company` / `hooks`."""
    return field.split(".", 1)[0].split("[", 1)[0]
