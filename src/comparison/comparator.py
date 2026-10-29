"""Compares the authoritative primary extraction with the independent secondary extraction.

The primary extraction is read-only here: nothing in this module modifies it.
"""

from __future__ import annotations

from collections import defaultdict

from comparison.scoring import mean
from comparison.table_comparator import compare_table, to_comparable
from models.evaluation import (
    DocumentEvaluation,
    EvaluationResult,
    ReasonCode,
    TableEvaluation,
)
from models.normalised_extraction import NormalisedExtraction, NormalisedTable
from models.response import ExtractionResponse


def match_tables(
    primary: list[NormalisedTable], secondary: list[NormalisedTable]
) -> list[tuple[NormalisedTable | None, ReasonCode | None]]:
    """Pair tables that start on the same page, in order, when both report the same count there.

    Returns one entry per primary table: the matched secondary table, or ``None`` with a reason.
    """
    secondary_by_page: dict[int, list[NormalisedTable]] = defaultdict(list)
    for table in secondary:
        secondary_by_page[table.first_page].append(table)
    primary_by_page: dict[int, list[int]] = defaultdict(list)
    for index, table in enumerate(primary):
        primary_by_page[table.first_page].append(index)

    result: list[tuple[NormalisedTable | None, ReasonCode | None]] = [(None, None)] * len(primary)
    for page, indexes in primary_by_page.items():
        candidates = secondary_by_page.get(page, [])
        for ordinal, index in enumerate(indexes):
            if not candidates:
                result[index] = (None, ReasonCode.NO_COMPARABLE_SECONDARY_TABLE)
            elif len(candidates) != len(indexes):
                result[index] = (None, ReasonCode.AMBIGUOUS_TABLE_ALIGNMENT)
            else:
                result[index] = (candidates[ordinal], None)
    return result


def _document_reason(same_table_count: bool) -> ReasonCode:
    return ReasonCode.PARTIAL_AGREEMENT if same_table_count else ReasonCode.TABLE_COUNT_MISMATCH


def compare_extractions(primary: ExtractionResponse, secondary: NormalisedExtraction) -> EvaluationResult:
    primary_tables = [to_comparable(t) for t in primary.tables]
    tables: list[TableEvaluation] = []
    for index, (p_table, (s_table, reason)) in enumerate(
        zip(primary_tables, match_tables(primary_tables, secondary.tables), strict=True)
    ):
        if s_table is None:
            tables.append(TableEvaluation(table_index=index, agreement=None, reason=reason))
            continue
        agreement, table_reason, cells = compare_table(p_table, s_table)
        tables.append(TableEvaluation(table_index=index, agreement=agreement, reason=table_reason, cells=cells))

    units = [t.agreement for t in tables if t.agreement is not None]
    # Equal table counts only count as evidence when some table was actually compared.
    # Unequal counts are always disagreement, even when no table could be paired.
    same_count = len(primary.tables) == len(secondary.tables)
    if units or not same_count:
        units.append(1.0 if same_count else 0.0)
    document_score = mean(units)
    if document_score is None:
        document = DocumentEvaluation(agreement=None, reason=ReasonCode.NO_COMPARABLE_ELEMENTS)
    else:
        document = DocumentEvaluation(
            agreement=document_score,
            reason=None if document_score >= 1.0 else _document_reason(same_count),
        )

    return EvaluationResult(secondary_model=secondary.source_model, document=document, tables=tables)
