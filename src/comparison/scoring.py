"""The scoring policy: every number that turns agreement evidence into a score lives here.

Change scoring by changing these constants, not the extraction or comparison code.
There are no fuzzy-match thresholds in V1: values either match exactly after
``common.text.normalise_value`` (whitespace runs collapsed), or they do not.
"""

from __future__ import annotations

from collections.abc import Iterable

# Cell scores.
CELL_MATCH = 1.0
CELL_SPAN_MISMATCH = 0.5  # same value, different row/column span
CELL_VALUE_MISMATCH = 0.0
CELL_MERGED = 0.0  # one extractor merged visually separate cells

# Table score = STRUCTURE_WEIGHT * structure + CELLS_WEIGHT * mean(cell scores).
TABLE_STRUCTURE_WEIGHT = 0.25
TABLE_CELLS_WEIGHT = 0.75


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(sum(items) / len(items), 4) if items else None
