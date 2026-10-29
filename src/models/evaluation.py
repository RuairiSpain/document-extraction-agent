"""Evaluation models.

``agreement`` is the *independent extraction agreement* between the GPT extraction and the
Mistral OCR extraction. It is not a Document Intelligence confidence value and not a
model's subjective certainty. ``None`` means no comparison
was possible, which is different from ``0.0`` (the extractions disagree).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

EVALUATION_METHOD = "independent-extraction-comparison"


class ReasonCode(StrEnum):
    # Unavailable (agreement is null)
    NO_COMPARABLE_SECONDARY_TABLE = "NO_COMPARABLE_SECONDARY_TABLE"
    AMBIGUOUS_TABLE_ALIGNMENT = "AMBIGUOUS_TABLE_ALIGNMENT"
    NO_COMPARABLE_SECONDARY_CELL = "NO_COMPARABLE_SECONDARY_CELL"
    NO_COMPARABLE_CELLS = "NO_COMPARABLE_CELLS"
    NO_COMPARABLE_ELEMENTS = "NO_COMPARABLE_ELEMENTS"
    SECONDARY_EXTRACTION_UNAVAILABLE = "SECONDARY_EXTRACTION_UNAVAILABLE"
    SECONDARY_DOCUMENT_LIMIT_EXCEEDED = "SECONDARY_DOCUMENT_LIMIT_EXCEEDED"
    COMPARISON_FAILED = "COMPARISON_FAILED"
    # Explanations for scored disagreement (agreement < 1.0)
    VALUE_MISMATCH = "VALUE_MISMATCH"
    SPAN_MISMATCH = "SPAN_MISMATCH"
    MERGED_CELLS_IN_SECONDARY = "MERGED_CELLS_IN_SECONDARY"
    MERGED_CELLS_IN_PRIMARY = "MERGED_CELLS_IN_PRIMARY"
    STRUCTURE_MISMATCH = "STRUCTURE_MISMATCH"
    TABLE_COUNT_MISMATCH = "TABLE_COUNT_MISMATCH"
    CELL_DISAGREEMENT = "CELL_DISAGREEMENT"
    PARTIAL_AGREEMENT = "PARTIAL_AGREEMENT"


class _EvalModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class _Scored(_EvalModel):
    agreement: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: ReasonCode | None = None


class DocumentEvaluation(_Scored):
    pass


class CellEvaluation(_Scored):
    row_index: int
    column_index: int


class TableEvaluation(_Scored):
    table_index: int
    cells: list[CellEvaluation] = Field(default_factory=list)


class EvaluationWarning(_EvalModel):
    code: str
    message: str


class EvaluationResult(_EvalModel):
    enabled: bool = True
    method: str = EVALUATION_METHOD
    secondary_model: str
    document: DocumentEvaluation
    tables: list[TableEvaluation] = Field(default_factory=list)
    warnings: list[EvaluationWarning] = Field(default_factory=list)
