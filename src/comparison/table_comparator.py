"""Table comparison: structure, positional cell agreement and merged-cell detection.

For a matched pair of tables, every primary cell gets one outcome:

1. Merged in secondary: a secondary cell's text equals the text of two or more consecutive,
   non-empty primary cells (same row or same column, starting at the same anchor) joined by a
   space or nothing -> ``cell_merged`` (``MERGED_CELLS_IN_SECONDARY``). Also applies when the
   primary anchor is covered by a secondary span that starts elsewhere.
2. Merged in primary: the reverse -> ``cell_merged`` (``MERGED_CELLS_IN_PRIMARY``).
3. Same anchor in both: equal normalised value and spans -> ``cell_match``; equal value but
   different spans -> ``cell_span_mismatch``; different value -> ``cell_value_mismatch``.
4. No secondary cell at or covering the position -> ``None`` (``NO_COMPARABLE_SECONDARY_CELL``).

Merge detection compares whitespace-collapsed text; it decides structure only, never values.

Example. The page shows a row with two cells, ``5`` and ``8``:

    GPT extraction     |  5  |  8  |   two cells
    Mistral extraction |  5 8      |   one cell

The Mistral cell text ("5 8") equals the two GPT cells joined by a space, so both GPT cells are
marked ``MERGED_CELLS_IN_SECONDARY`` and score ``CELL_MERGED`` (0.0). This is the failure the
agent exists to catch, so it is scored as a disagreement and not as a value mismatch.
"""

from __future__ import annotations

from common.text import collapse_whitespace, normalise_value
from comparison import scoring
from comparison.scoring import mean
from models.evaluation import CellEvaluation, ReasonCode
from models.normalised_extraction import NormalisedCell, NormalisedTable
from models.primary_output import CellKind, TableOutput

Anchor = tuple[int, int]


def to_comparable(table: TableOutput) -> NormalisedTable:
    return NormalisedTable(
        page_numbers=sorted(set(table.page_numbers)),
        row_count=table.row_count,
        column_count=table.column_count,
        cells=[
            NormalisedCell(
                row_index=c.row_index,
                column_index=c.column_index,
                row_span=c.row_span,
                column_span=c.column_span,
                content=c.content,
                is_header=c.kind is not CellKind.CONTENT,
            )
            for c in table.cells
        ],
    )


def _anchors(table: NormalisedTable) -> dict[Anchor, NormalisedCell]:
    return {(c.row_index, c.column_index): c for c in table.cells}


def _coverage(table: NormalisedTable) -> dict[Anchor, NormalisedCell]:
    covered: dict[Anchor, NormalisedCell] = {}
    for cell in table.cells:
        for r in range(cell.row_index, cell.row_index + cell.row_span):
            for c in range(cell.column_index, cell.column_index + cell.column_span):
                covered[(r, c)] = cell
    return covered


def merged_run(target: NormalisedCell, others: dict[Anchor, NormalisedCell]) -> list[NormalisedCell]:
    """Return the >= 2 consecutive cells in ``others`` whose joined text equals ``target``'s text."""
    goal = collapse_whitespace(target.content)
    if not goal:
        return []
    for step in ("row", "column"):
        run: list[NormalisedCell] = []
        position = (target.row_index, target.column_index)
        while position in others:
            cell = others[position]
            text = collapse_whitespace(cell.content)
            if not text:
                break
            run.append(cell)
            parts = [collapse_whitespace(c.content) for c in run]
            if len(run) >= 2 and goal in (" ".join(parts), "".join(parts)):
                return run
            if len("".join(parts)) >= len(goal):
                break
            position = (
                (cell.row_index, cell.column_index + cell.column_span)
                if step == "row"
                else (cell.row_index + cell.row_span, cell.column_index)
            )
    return []


def compare_table(
    primary: NormalisedTable, secondary: NormalisedTable
) -> tuple[float | None, ReasonCode | None, list[CellEvaluation]]:
    p_anchor, s_anchor, s_cover = _anchors(primary), _anchors(secondary), _coverage(secondary)
    outcomes: dict[Anchor, tuple[float | None, ReasonCode | None]] = {}

    for s_cell in secondary.cells:
        for p_cell in merged_run(s_cell, p_anchor):
            outcomes[(p_cell.row_index, p_cell.column_index)] = (
                scoring.CELL_MERGED,
                ReasonCode.MERGED_CELLS_IN_SECONDARY,
            )

    for key, p_cell in p_anchor.items():
        if key in outcomes:
            continue
        if merged_run(p_cell, s_anchor):
            outcomes[key] = (scoring.CELL_MERGED, ReasonCode.MERGED_CELLS_IN_PRIMARY)
            continue
        s_cell = s_anchor.get(key)
        if s_cell is None:
            outcomes[key] = (
                (scoring.CELL_MERGED, ReasonCode.MERGED_CELLS_IN_SECONDARY)
                if key in s_cover
                else (None, ReasonCode.NO_COMPARABLE_SECONDARY_CELL)
            )
            continue
        same_value = normalise_value(p_cell.content) == normalise_value(s_cell.content)
        same_span = (p_cell.row_span, p_cell.column_span) == (s_cell.row_span, s_cell.column_span)
        if not same_value:
            outcomes[key] = (scoring.CELL_VALUE_MISMATCH, ReasonCode.VALUE_MISMATCH)
        elif not same_span:
            outcomes[key] = (scoring.CELL_SPAN_MISMATCH, ReasonCode.SPAN_MISMATCH)
        else:
            outcomes[key] = (scoring.CELL_MATCH, None)

    cells = [
        CellEvaluation(row_index=r, column_index=c, agreement=outcomes[(r, c)][0], reason=outcomes[(r, c)][1])
        for r, c in sorted(p_anchor)
    ]

    scored = [c.agreement for c in cells if c.agreement is not None]
    if not scored:
        return None, ReasonCode.NO_COMPARABLE_CELLS, cells
    structure = (primary.row_count == secondary.row_count) / 2 + (primary.column_count == secondary.column_count) / 2
    agreement = round(scoring.TABLE_STRUCTURE_WEIGHT * structure + scoring.TABLE_CELLS_WEIGHT * mean(scored), 4)
    if agreement >= 1.0:
        return 1.0, None, cells
    reason = ReasonCode.STRUCTURE_MISMATCH if structure < 1.0 else ReasonCode.CELL_DISAGREEMENT
    return agreement, reason, cells
