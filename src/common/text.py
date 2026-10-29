"""Deterministic text helpers shared by the extraction and comparison code.

Nothing in this module alters digits, separators, currency symbols, dates,
signs or percentages.
"""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def normalise_value(value: str | None) -> str | None:
    """Comparison-only normalisation: runs of whitespace (including line breaks) become one space.

    ``Net\nAmount`` and ``Net Amount`` are the same text to a reader. Raw values are never mutated.
    """
    return None if value is None else collapse_whitespace(value)


def collapse_whitespace(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()
