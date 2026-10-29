"""The extraction schema: the GPT model fills it in, and the response returns it.

Strict structured output needs every field to be required, with no defaults or free-form
dictionaries. JSON names are camelCase.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class CellKind(StrEnum):
    CONTENT = "content"
    ROW_HEADER = "rowHeader"
    COLUMN_HEADER = "columnHeader"
    STUB_HEAD = "stubHead"
    DESCRIPTION = "description"


class _Strict(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


def validate_table_grid(row_count: int, column_count: int, cells) -> None:
    """Reject cells outside the declared grid and cells that overlap. Raises ValueError."""
    occupied: dict[tuple[int, int], int] = {}
    for position, cell in enumerate(cells):
        last_row = cell.row_index + cell.row_span - 1
        last_col = cell.column_index + cell.column_span - 1
        if last_row >= row_count or last_col >= column_count:
            raise ValueError(
                f"cell {position} at ({cell.row_index},{cell.column_index}) with span "
                f"({cell.row_span},{cell.column_span}) exceeds table size ({row_count}x{column_count})"
            )
        for r in range(cell.row_index, last_row + 1):
            for c in range(cell.column_index, last_col + 1):
                if (r, c) in occupied:
                    raise ValueError(f"cell {position} overlaps cell {occupied[(r, c)]} at grid position ({r},{c})")
                occupied[(r, c)] = position


class ParagraphOutput(_Strict):
    page_number: int = Field(description="1-based page number on which the paragraph appears.")
    content: str = Field(description="Literal paragraph text exactly as it appears.")


class CellOutput(_Strict):
    kind: CellKind = Field(description="columnHeader/rowHeader/stubHead for headers, content for data.")
    row_index: int = Field(description="0-based logical row index in the complete logical table.")
    column_index: int = Field(description="0-based logical column index.")
    row_span: int = Field(description="Number of logical rows the visual cell spans (>= 1).")
    column_span: int = Field(description="Number of logical columns the visual cell spans (>= 1).")
    content: str = Field(description="Literal cell text. Empty string for a blank cell.")
    page_number: int = Field(description="1-based page on which this cell is printed.")


class TableOutput(_Strict):
    page_numbers: list[int] = Field(description="All 1-based pages the logical table occupies, ascending.")
    row_count: int = Field(description="Number of logical rows, excluding repeated headers on continuation pages.")
    column_count: int = Field(description="Number of logical columns.")
    cells: list[CellOutput] = Field(
        description="Every visually distinct cell, including structurally significant blank cells."
    )

    @model_validator(mode="after")
    def _check(self) -> TableOutput:
        if not self.page_numbers or not self.cells:
            raise ValueError("a table needs at least one page and one cell")
        for cell in self.cells:
            if cell.row_span < 1 or cell.column_span < 1 or cell.row_index < 0 or cell.column_index < 0:
                raise ValueError("cell indexes must be >= 0 and spans >= 1")
            if cell.page_number not in self.page_numbers:
                raise ValueError(f"cell page {cell.page_number} not listed in table page_numbers")
        if self.row_count < 1 or self.column_count < 1:
            raise ValueError("row_count and column_count must be >= 1")
        validate_table_grid(self.row_count, self.column_count, self.cells)
        return self


class PrimaryModelOutput(_Strict):
    paragraphs: list[ParagraphOutput] = Field(
        description="All non-table text in reading order across the whole document."
    )
    tables: list[TableOutput] = Field(description="All tables in document order.")

    def validate_pages(self, page_count: int) -> None:
        pages = [p.page_number for p in self.paragraphs]
        for table in self.tables:
            pages += table.page_numbers
        bad = sorted({p for p in pages if p < 1 or p > page_count})
        if bad:
            raise ValueError(f"page numbers {bad} are outside the document (1..{page_count})")
