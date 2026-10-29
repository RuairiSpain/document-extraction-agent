"""Normalises raw Mistral OCR output into ``NormalisedExtraction`` for comparison only.

Rules (all deterministic):

* Tables come from ``pages[].tables[].content`` (HTML, requested with ``table_format=html``).
  If a page has no separate tables, inline HTML tables and then Markdown pipe tables in
  ``pages[].markdown`` are used.
* Mistral reports tables per page. A table that is the last content of page *n* is merged with a
  table that is the first content of page *n+1* when both have the same column count, because the
  GPT extraction returns such a table as one. If the continuation repeats the first row of the
  table, that row is dropped as a repeated header. Without page markdown the position of a table
  is unknown and nothing is merged.
* Images, page headers/footers and nested tables are not compared; they are listed in
  ``unsupported_elements``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from common.text import collapse_whitespace
from models.errors import SecondaryError
from models.evaluation import ReasonCode
from models.normalised_extraction import NormalisedExtraction, NormalisedTable
from secondary.table_parsing import HTML_TABLE, ParsedTable, find_pipe_tables, parse_html_tables


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _MistralTable(_Lenient):
    id: str | None = None
    content: str | None = None


class _MistralPage(_Lenient):
    index: int
    markdown: str = ""
    tables: list[_MistralTable] = Field(default_factory=list)
    images: list[Any] = Field(default_factory=list)
    header: str | None = None
    footer: str | None = None


class _MistralResponse(_Lenient):
    pages: list[_MistralPage]


def _invalid(message: str) -> SecondaryError:
    return SecondaryError(ReasonCode.SECONDARY_EXTRACTION_UNAVAILABLE, message)


def _to_table(parsed: ParsedTable, page_number: int) -> NormalisedTable:
    return NormalisedTable(
        page_numbers=[page_number],
        row_count=parsed.row_count,
        column_count=parsed.column_count,
        cells=parsed.cells,
    )


@dataclass
class _PageTable:
    table: NormalisedTable
    page_number: int
    leading: bool  # nothing precedes it on its page
    trailing: bool  # nothing follows it on its page


def _flags(markdown: str, start: int | None, end: int | None) -> tuple[bool, bool]:
    if start is None or end is None:
        return False, False
    return markdown[:start].strip() == "", markdown[end:].strip() == ""


def _placeholder_span(markdown: str, table_id: str | None) -> tuple[int | None, int | None]:
    """Where Mistral's page markdown refers to a table, e.g. ``[tbl-0.html](tbl-0.html)``."""
    if not table_id:
        return None, None
    match = re.search(r"!?\[[^\]]*\]\(" + re.escape(table_id) + r"\)", markdown)
    return (match.start(), match.end()) if match else (None, None)


def _tables_for_page(page: _MistralPage, page_number: int, unsupported: set[str]) -> list[_PageTable]:
    markdown = page.markdown or ""
    found: list[tuple[ParsedTable, int | None, int | None]] = []
    if page.tables:
        for table in page.tables:
            content = table.content or ""
            start, end = _placeholder_span(markdown, table.id)
            parsed = parse_html_tables(content) or [m.table for m in find_pipe_tables(content)]
            found.extend((p, start, end) for p in parsed)
    else:
        for match in HTML_TABLE.finditer(markdown):
            found.extend((p, match.start(), match.end()) for p in parse_html_tables(match.group(0)))
        if not found:
            found = [(m.table, m.start, m.end) for m in find_pipe_tables(markdown)]
    if any(parsed.nested_tables for parsed, _, _ in found):
        unsupported.add("nested tables")
    results = [_PageTable(_to_table(p, page_number), page_number, *_flags(markdown, s, e)) for p, s, e in found]
    # Only the first table on a page can lead it, and only the last can trail it.
    for index, item in enumerate(results):
        item.leading = item.leading and index == 0
        item.trailing = item.trailing and index == len(results) - 1
    return results


def _first_row(table: NormalisedTable) -> list[str]:
    cells = sorted((c for c in table.cells if c.row_index == 0), key=lambda c: c.column_index)
    return [collapse_whitespace(c.content) for c in cells]


def _append(previous: NormalisedTable, continuation: NormalisedTable) -> NormalisedTable:
    repeated = (
        continuation.row_count > 1
        and _first_row(previous) == _first_row(continuation)
        and all(c.row_span == 1 for c in continuation.cells if c.row_index == 0)
    )
    dropped = 1 if repeated else 0
    offset = previous.row_count - dropped
    appended = [
        cell.model_copy(update={"row_index": cell.row_index + offset})
        for cell in continuation.cells
        if not (repeated and cell.row_index == 0)
    ]
    return NormalisedTable(
        page_numbers=sorted({*previous.page_numbers, *continuation.page_numbers}),
        row_count=previous.row_count + continuation.row_count - dropped,
        column_count=previous.column_count,
        cells=[*previous.cells, *appended],
    )


def _merge_continued(page_tables: list[_PageTable]) -> list[NormalisedTable]:
    merged: list[_PageTable] = []
    for item in page_tables:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.trailing
            and item.leading
            and item.page_number == previous.page_number + 1
            and previous.table.column_count == item.table.column_count
        ):
            previous.table = _append(previous.table, item.table)
            previous.trailing = item.trailing
            previous.page_number = item.page_number
        else:
            merged.append(item)
    return [m.table for m in merged]


def normalise(
    raw: dict[str, Any],
    *,
    source_model: str,
    page_count: int,
) -> NormalisedExtraction:
    try:
        response = _MistralResponse.model_validate(raw)
    except ValidationError:
        raise _invalid("The secondary output did not match the expected OCR response shape.") from None

    unsupported: set[str] = set()
    page_tables: list[_PageTable] = []
    for page in sorted(response.pages, key=lambda p: p.index):
        page_number = page.index + 1
        if not 1 <= page_number <= page_count:
            raise _invalid("The secondary output referenced a page outside the document.")
        if page.images:
            unsupported.add("images")
        if page.header or page.footer:
            unsupported.add("page headers/footers")
        try:
            page_tables.extend(_tables_for_page(page, page_number, unsupported))
        except ValidationError:
            raise _invalid("A secondary table could not be normalised.") from None

    return NormalisedExtraction(
        source_model=source_model,
        page_count=page_count,
        tables=_merge_continued(page_tables),
        unsupported_elements=sorted(unsupported),
    )
