"""Live scenarios against Microsoft Foundry. Run with ``pytest -m integration``."""

from __future__ import annotations

import base64

import pytest

from models.request import ExtractionRequest
from tests.integration.conftest import run_pro

pytestmark = pytest.mark.integration


def _request(pdf: bytes, model: str = "gpt-5.4-mini", evaluation: bool = False) -> ExtractionRequest:
    return ExtractionRequest.model_validate(
        {
            "document": {"content": base64.b64encode(pdf).decode(), "contentType": "application/pdf"},
            "extraction": {"model": model},
            "evaluation": {"enabled": evaluation},
        }
    )


def _grid(table) -> dict[tuple[int, int], str]:
    return {(c.row_index, c.column_index): c.content for c in table.cells}


async def test_adjacent_cells_stay_separate(live, fixture_pdfs) -> None:
    result = await live.run(_request(fixture_pdfs["adjacent_numeric_cells"]))
    grid = _grid(result.tables[0])
    assert grid[(1, 1)] == "5" and grid[(1, 2)] == "8" and result.evaluation is None


async def test_merged_headings_have_spans(live, fixture_pdfs) -> None:
    result = await live.run(_request(fixture_pdfs["merged_headings"]))
    assert any(c.column_span > 1 or c.row_span > 1 for c in result.tables[0].cells)


async def test_table_across_pages(live, fixture_pdfs) -> None:
    result = await live.run(_request(fixture_pdfs["repeated_headers"]))
    assert any(len(t.page_numbers) >= 2 for t in result.tables)


async def test_cross_check_scores_agreement(live, fixture_pdfs) -> None:
    result = await live.run(_request(fixture_pdfs["adjacent_numeric_cells"], evaluation=True))
    assert result.evaluation is not None and result.evaluation.document.agreement is not None


@pytest.mark.skipif(not run_pro(), reason="Set RUN_PRO_INTEGRATION=1 to run the slower, costlier Pro test.")
async def test_pro_model(live, fixture_pdfs) -> None:
    result = await live.run(_request(fixture_pdfs["adjacent_numeric_cells"], model="gpt-5.4-pro"))
    assert result.model_id == "gpt-5.4-pro" and _grid(result.tables[0])[(1, 2)] == "8"
