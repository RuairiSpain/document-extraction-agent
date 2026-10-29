"""Cheap, local PDF inspection: validity, page count and page dimensions."""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from functools import cached_property

from pypdf import PdfReader

from models.errors import AppError, ErrorCode

POINTS_PER_INCH = 72.0
# Documented Responses API limit: each file < 50 MB.
MAX_PDF_BYTES = 50 * 1024 * 1024

# pypdf logs warnings that can include fragments of document structure; keep them quiet.
logging.getLogger("pypdf").setLevel(logging.ERROR)


@dataclass(frozen=True)
class PageInfo:
    page_number: int
    width_inches: float
    height_inches: float


@dataclass(frozen=True)
class PdfDocument:
    data: bytes
    pages: tuple[PageInfo, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @cached_property
    def data_uri(self) -> str:
        """Encoded once and shared by the GPT and Mistral calls."""
        return f"data:application/pdf;base64,{base64.b64encode(self.data).decode('ascii')}"


def _page_info(page_number: int, page) -> PageInfo:
    width = float(page.mediabox.width) / POINTS_PER_INCH
    height = float(page.mediabox.height) / POINTS_PER_INCH
    if page.rotation % 180:  # a page rotated by 90/270 degrees is displayed sideways
        width, height = height, width
    return PageInfo(page_number, round(width, 4), round(height, 4))


def inspect_pdf(data: bytes) -> PdfDocument:
    """Validate the PDF and read page sizes."""
    if len(data) > MAX_PDF_BYTES:
        raise AppError(ErrorCode.DOCUMENT_TOO_LARGE, f"The document is larger than {MAX_PDF_BYTES} bytes.")
    try:
        reader = PdfReader(io.BytesIO(data))
        # pypdf already tries the empty password, which opens PDFs that only restrict printing or copying.
        if reader.is_encrypted and not reader.decrypt(""):
            raise AppError(ErrorCode.INVALID_REQUEST, "The PDF is password-protected.")
        pages = tuple(_page_info(i + 1, page) for i, page in enumerate(reader.pages))
    except AppError:
        raise
    except Exception:  # pypdf raises many types: empty, corrupt, encrypted
        raise AppError(ErrorCode.INVALID_REQUEST, "The document is not a readable PDF.") from None
    if not pages:
        raise AppError(ErrorCode.INVALID_REQUEST, "The PDF has no pages.")
    return PdfDocument(data=data, pages=pages)
