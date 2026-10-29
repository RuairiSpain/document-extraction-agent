"""Internal comparison representation of an independent secondary extraction.

Only the information needed for deterministic comparison is kept. It is never
returned to callers.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.primary_output import validate_table_grid


class _Normalised(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NormalisedCell(_Normalised):
    row_index: int = Field(ge=0)
    column_index: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    content: str
    is_header: bool = False


class NormalisedTable(_Normalised):
    page_numbers: list[int]
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    cells: list[NormalisedCell]

    @property
    def first_page(self) -> int:
        return min(self.page_numbers)

    @model_validator(mode="after")
    def _check_grid(self) -> NormalisedTable:
        if self.cells:
            validate_table_grid(self.row_count, self.column_count, self.cells)
        return self


class NormalisedExtraction(_Normalised):
    source_model: str
    page_count: int = Field(ge=0)
    tables: list[NormalisedTable] = Field(default_factory=list)
    unsupported_elements: list[str] = Field(
        default_factory=list, description="Secondary output kinds that were ignored for comparison."
    )
