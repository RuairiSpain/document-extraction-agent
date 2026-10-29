"""Cross-check: parsing Mistral output and scoring agreement."""

from __future__ import annotations

import pytest

from comparison.comparator import compare_extractions
from comparison.table_comparator import compare_table
from extraction.extractor import to_response
from models.errors import SecondaryError
from models.evaluation import ReasonCode
from models.normalised_extraction import NormalisedCell, NormalisedExtraction, NormalisedTable
from secondary.normaliser import normalise
from tests.conftest import adjacent_cells_output, pdf_from_bytes


def html(rows: list[list[str]]) -> str:
    body = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows)
    return f"<table>{body}</table>"


def secondary_with(rows: list[list[str]]) -> NormalisedExtraction:
    cells = [
        NormalisedCell(row_index=r, column_index=c, content=v) for r, row in enumerate(rows) for c, v in enumerate(row)
    ]
    table = NormalisedTable(page_numbers=[1], row_count=len(rows), column_count=len(rows[0]), cells=cells)
    return NormalisedExtraction(source_model="mistral-ocr-4-0", page_count=1, tables=[table])


@pytest.fixture
def primary(fixture_pdfs):
    return to_response(adjacent_cells_output(), pdf_from_bytes(fixture_pdfs["adjacent_numeric_cells"]), "gpt-5.4-mini")


def test_identical_extractions_agree_fully(primary) -> None:
    result = compare_extractions(primary, secondary_with([["Product", "Quantity", "Rate"], ["A", "5", "8"]]))
    assert result.document.agreement == 1.0 and result.document.reason is None


def test_changed_value_lowers_agreement_for_that_cell_only(primary) -> None:
    result = compare_extractions(primary, secondary_with([["Product", "Quantity", "Rate"], ["A", "5", "9"]]))
    cells = {(c.row_index, c.column_index): c for c in result.tables[0].cells}
    assert cells[(1, 2)].reason is ReasonCode.VALUE_MISMATCH and cells[(1, 2)].agreement == 0.0
    assert cells[(1, 1)].agreement == 1.0
    assert 0 < result.document.agreement < 1


def test_merged_adjacent_cells_in_the_cross_check_are_flagged(primary) -> None:
    result = compare_extractions(primary, secondary_with([["Product", "Quantity Rate"], ["A", "5 8"]]))
    cells = {(c.row_index, c.column_index): c for c in result.tables[0].cells}
    assert cells[(1, 1)].reason is ReasonCode.MERGED_CELLS_IN_SECONDARY
    assert cells[(1, 2)].reason is ReasonCode.MERGED_CELLS_IN_SECONDARY
    assert result.document.agreement < 1


def test_missing_tables_in_the_cross_check_count_as_disagreement(primary) -> None:
    empty = NormalisedExtraction(source_model="mistral-ocr-4-0", page_count=1)
    result = compare_extractions(primary, empty)
    assert result.document.agreement == 0.0  # the extractions disagree on the number of tables
    assert result.document.reason is ReasonCode.TABLE_COUNT_MISMATCH


def test_normaliser_reads_html_tables_with_spans() -> None:
    raw = {
        "pages": [
            {
                "index": 0,
                "tables": [
                    {"id": "t", "content": "<table><tr><th colspan=2>H</th></tr><tr><td>1</td><td>2</td></tr></table>"}
                ],
            }
        ]
    }
    table = normalise(raw, source_model="m", page_count=1).tables[0]
    assert (table.row_count, table.column_count) == (2, 2)
    assert table.cells[0].column_span == 2


def test_normaliser_falls_back_to_markdown_tables() -> None:
    raw = {"pages": [{"index": 0, "markdown": "| a | b |\n|---|---|\n| 1 | 2 |\n"}]}
    assert normalise(raw, source_model="m", page_count=1).tables[0].column_count == 2


def test_normaliser_rejects_pages_outside_the_document() -> None:
    with pytest.raises(SecondaryError):
        normalise({"pages": [{"index": 5, "markdown": ""}]}, source_model="m", page_count=1)


def test_line_breaks_in_text_are_not_a_value_mismatch() -> None:
    def one_cell(text: str) -> NormalisedTable:
        cell = NormalisedCell(row_index=0, column_index=0, content=text)
        return NormalisedTable(page_numbers=[1], row_count=1, column_count=1, cells=[cell])

    assert compare_table(one_cell("Net Amount"), one_cell("Net\nAmount"))[0] == 1.0
    assert compare_table(one_cell("Net Amount"), one_cell("Net Amounts"))[0] < 1.0


def _two_page_response(pdf_pages: int = 2):
    from types import SimpleNamespace

    from common.pdf import PageInfo
    from models.primary_output import PrimaryModelOutput
    from tests.conftest import cell, table

    rows = [["H1", "H2"], *[[f"a{i}", f"b{i}"] for i in range(1, 7)]]
    cells = [
        cell(r, c, v, kind="columnHeader" if r == 0 else "content", page=1 if r < 4 else 2)
        for r, row in enumerate(rows)
        for c, v in enumerate(row)
    ]
    pdf = SimpleNamespace(page_count=2, pages=(PageInfo(1, 8.27, 11.69), PageInfo(2, 8.27, 11.69)))
    output = PrimaryModelOutput(paragraphs=[], tables=[table(7, 2, cells, pages=[1, 2])])
    return to_response(output, pdf, "m"), rows


def _mistral_pages(rows, repeat_header: bool) -> dict:
    second = [rows[0], *rows[4:]] if repeat_header else rows[4:]
    return {
        "pages": [
            {
                "index": 0,
                "markdown": "[tbl-0.html](tbl-0.html)",
                "tables": [{"id": "tbl-0.html", "content": html(rows[:4])}],
            },
            {
                "index": 1,
                "markdown": "[tbl-1.html](tbl-1.html)",
                "tables": [{"id": "tbl-1.html", "content": html(second)}],
            },
        ]
    }


@pytest.mark.parametrize("repeat_header", [False, True])
def test_a_table_split_by_page_in_the_cross_check_is_merged(repeat_header) -> None:
    response, rows = _two_page_response()
    secondary = normalise(_mistral_pages(rows, repeat_header), source_model="m", page_count=2)
    assert len(secondary.tables) == 1 and secondary.tables[0].row_count == 7
    result = compare_extractions(response, secondary)
    assert result.document.agreement == 1.0


def test_tables_are_not_merged_when_text_follows_the_first_one() -> None:
    _, rows = _two_page_response()
    raw = _mistral_pages(rows, False)
    raw["pages"][0]["markdown"] = "[tbl-0.html](tbl-0.html)\n\nA closing paragraph."
    assert len(normalise(raw, source_model="m", page_count=2).tables) == 2
