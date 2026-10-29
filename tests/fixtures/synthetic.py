"""Synthetic PDF fixtures, generated at test time with reportlab.

No customer documents are used. Each builder returns PDF bytes.
"""

from __future__ import annotations

import io
from collections.abc import Callable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

_STYLES = getSampleStyleSheet()
_GRID = TableStyle([("GRID", (0, 0), (-1, -1), 0.75, colors.black), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")])


def _build(flowables: list) -> bytes:
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, title="synthetic fixture").build(flowables)
    return buffer.getvalue()


def _table(rows: list[list[str]], style: TableStyle = _GRID, **kwargs) -> Table:
    table = Table(rows, **kwargs)
    table.setStyle(style)
    return table


def adjacent_numeric_cells() -> bytes:
    return _build(
        [
            Paragraph("Adjacent numeric cells", _STYLES["Heading2"]),
            _table([["Product", "Quantity", "Rate"], ["A", "5", "8"]], colWidths=[80, 40, 40]),
        ]
    )


def merged_headings() -> bytes:
    style = TableStyle([*_GRID.getCommands(), ("SPAN", (1, 0), (2, 0)), ("SPAN", (0, 0), (0, 1))])
    rows = [["Region", "Sales", ""], ["", "Q1", "Q2"], ["North", "100", "120"], ["South", "90", "95"]]
    return _build([Paragraph("Merged headings", _STYLES["Heading2"]), _table(rows, style)])


def _long_rows(count: int) -> list[list[str]]:
    return [["Line", "Account", "Amount"], *[[str(i), f"ACC-{i:03d}", f"{i * 1.25:.2f}"] for i in range(1, count + 1)]]


def repeated_headers() -> bytes:
    return _build([Paragraph("Repeated headers", _STYLES["Heading2"]), _table(_long_rows(70), repeatRows=1)])


FIXTURES: dict[str, Callable[[], bytes]] = {
    "adjacent_numeric_cells": adjacent_numeric_cells,
    "merged_headings": merged_headings,
    "repeated_headers": repeated_headers,
}
