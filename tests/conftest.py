"""Shared test helpers. Unit tests never touch Azure."""

from __future__ import annotations

from typing import Any

import pytest

from common.pdf import PdfDocument, inspect_pdf
from config import Settings
from models.primary_output import CellOutput, PrimaryModelOutput, TableOutput
from tests.fixtures.synthetic import FIXTURES


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "FOUNDRY_PROJECT_ENDPOINT": "https://example-account.services.ai.azure.com/api/projects/example",
        "EXTRACTION_MODEL_MINI": "deploy-mini",
        "EXTRACTION_MODEL_PRO": "deploy-pro",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def fixture_pdfs() -> dict[str, bytes]:
    return {name: build() for name, build in FIXTURES.items()}


def pdf_from_bytes(data: bytes) -> PdfDocument:
    return inspect_pdf(data)


def cell(r: int, c: int, content: str, *, kind: str = "content", rs: int = 1, cs: int = 1, page: int = 1) -> CellOutput:
    return CellOutput(
        kind=kind, row_index=r, column_index=c, row_span=rs, column_span=cs, content=content, page_number=page
    )


def table(rows: int, cols: int, cells: list[CellOutput], pages: list[int] | None = None) -> TableOutput:
    return TableOutput(page_numbers=pages or [1], row_count=rows, column_count=cols, cells=cells)


def adjacent_cells_output() -> PrimaryModelOutput:
    """The adjacent_numeric_cells fixture as a correct extraction: | A | 5 | 8 | are three separate cells."""
    header = [cell(0, i, name, kind="columnHeader") for i, name in enumerate(["Product", "Quantity", "Rate"])]
    row = [cell(1, i, value) for i, value in enumerate(["A", "5", "8"])]
    return PrimaryModelOutput(
        paragraphs=[{"page_number": 1, "content": "Adjacent numeric cells"}],
        tables=[table(2, 3, [*header, *row])],
    )
