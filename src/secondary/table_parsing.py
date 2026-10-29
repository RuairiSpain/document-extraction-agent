"""Parses Mistral OCR table output (HTML or Markdown pipe tables) into grid cells."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from models.normalised_extraction import NormalisedCell

_HTML_WS = re.compile(r"[ \t\r\n\f]+")
HTML_TABLE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
_PIPE_SEPARATOR = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


@dataclass
class RawCell:
    text: str
    row_span: int = 1
    column_span: int = 1
    is_header: bool = False


@dataclass
class ParsedTable:
    rows: list[list[RawCell]]
    nested_tables: int = 0
    cells: list[NormalisedCell] = field(default_factory=list)
    row_count: int = 0
    column_count: int = 0


def _span(value: str | None) -> int:
    try:
        return max(1, int(value)) if value is not None else 1
    except ValueError:
        return 1


class _TableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[ParsedTable] = []
        self._depth = 0
        self._rows: list[list[RawCell]] = []
        self._row: list[RawCell] | None = None
        self._cell: RawCell | None = None
        self._text: list[str] = []
        self._nested = 0

    def _close_cell(self) -> None:
        if self._cell is not None:
            lines = [_HTML_WS.sub(" ", part).strip() for part in "".join(self._text).split("\n")]
            self._cell.text = "\n".join(line for line in lines if line)
            (self._row if self._row is not None else self._ensure_row()).append(self._cell)
        self._cell = None
        self._text = []

    def _ensure_row(self) -> list[RawCell]:
        self._row = []
        return self._row

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None:
            self._rows.append(self._row)
        self._row = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows, self._nested = [], 0
            else:
                self._nested += 1
            return
        if self._depth != 1:
            if tag == "br" and self._cell is not None:
                self._text.append("\n")
            return
        if tag == "tr":
            self._close_row()
            self._row = []
        elif tag in ("td", "th"):
            self._close_cell()
            values = dict(attrs)
            self._cell = RawCell(
                text="",
                row_span=_span(values.get("rowspan")),
                column_span=_span(values.get("colspan")),
                is_header=tag == "th",
            )
        elif tag == "br" and self._cell is not None:
            self._text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._depth == 1:
                self._close_row()
                self.tables.append(ParsedTable(rows=self._rows, nested_tables=self._nested))
            self._depth = max(0, self._depth - 1)
        elif self._depth == 1 and tag == "tr":
            self._close_row()
        elif self._depth == 1 and tag in ("td", "th"):
            self._close_cell()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._text.append(data)


def place_cells(rows: list[list[RawCell]]) -> tuple[list[NormalisedCell], int, int]:
    """Lay out HTML-style rows (with rowspan/colspan) on a grid.

    Spans that would overlap an already occupied position (malformed HTML) or run past
    the last row are clamped so the resulting grid is always valid.
    """
    row_count = len(rows)
    occupied: set[tuple[int, int]] = set()
    placed: list[tuple[int, int, int, int, RawCell]] = []
    for r, row in enumerate(rows):
        c = 0
        for raw in row:
            while (r, c) in occupied:
                c += 1
            column_span = 1
            while column_span < raw.column_span and (r, c + column_span) not in occupied:
                column_span += 1
            row_span = 1
            while (
                row_span < raw.row_span
                and r + row_span < row_count
                and all((r + row_span, c + dc) not in occupied for dc in range(column_span))
            ):
                row_span += 1
            placed.append((r, c, row_span, column_span, raw))
            for dr in range(row_span):
                for dc in range(column_span):
                    occupied.add((r + dr, c + dc))
            c += column_span
    column_count = max((c + cs for _, c, _, cs, _ in placed), default=0)
    cells = [
        NormalisedCell(
            row_index=r,
            column_index=c,
            row_span=rs,
            column_span=cs,
            content=raw.text,
            is_header=raw.is_header,
        )
        for r, c, rs, cs, raw in placed
    ]
    return cells, row_count, column_count


def parse_html_tables(html: str) -> list[ParsedTable]:
    parser = _TableHtmlParser()
    parser.feed(html)
    parser.close()
    for table in parser.tables:
        table.cells, table.row_count, table.column_count = place_cells(table.rows)
    return [t for t in parser.tables if t.cells]


def _split_pipe_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells, current, escaped = [], [], False
    for char in body:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


@dataclass
class PipeTableMatch:
    start: int
    end: int
    table: ParsedTable


def find_pipe_tables(markdown: str) -> list[PipeTableMatch]:
    """Find GitHub-style pipe tables (a header row followed by a ``---`` separator row)."""
    matches: list[PipeTableMatch] = []
    lines = markdown.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    i = 0
    while i < len(lines) - 1:
        if lines[i].strip().startswith("|") and _PIPE_SEPARATOR.match(lines[i + 1].strip()):
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            header = [RawCell(text=t, is_header=True) for t in _split_pipe_row(lines[i])]
            body = [[RawCell(text=t) for t in _split_pipe_row(row)] for row in lines[i + 2 : j]]
            table = ParsedTable(rows=[header, *body])
            table.cells, table.row_count, table.column_count = place_cells(table.rows)
            matches.append(PipeTableMatch(start=offsets[i], end=offsets[j], table=table))
            i = j
        else:
            i += 1
    return matches
