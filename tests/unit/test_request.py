import base64
import io

import pytest
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

import common.pdf as pdf_module
from common.pdf import inspect_pdf
from models.errors import AppError, ErrorCode
from models.request import ExtractionRequest


def _body(content: str, content_type: str = "application/pdf", **extra) -> dict:
    return {"document": {"content": content, "contentType": content_type}, **extra}


def test_valid_request_defaults_to_mini_without_evaluation(fixture_pdfs) -> None:
    pdf = fixture_pdfs["adjacent_numeric_cells"]
    request = ExtractionRequest.model_validate(_body(base64.b64encode(pdf).decode()))
    assert request.extraction.model.value == "gpt-5.4-mini"
    assert request.evaluation.enabled is False
    assert request.document.pdf_bytes == pdf


@pytest.mark.parametrize(
    "body",
    [
        _body("not base64!"),
        _body("JVBERi0=", content_type="image/png"),
        _body("JVBERi0=", extraction={"model": "gpt-4o"}),
        _body("JVBERi0=", surprise=True),
    ],
)
def test_invalid_requests_are_rejected(body) -> None:
    with pytest.raises(ValidationError):
        ExtractionRequest.model_validate(body)


def test_inspect_pdf_reads_page_size(fixture_pdfs) -> None:
    pdf = inspect_pdf(fixture_pdfs["adjacent_numeric_cells"])
    assert pdf.page_count == 1 and 8 < pdf.pages[0].width_inches < 8.5  # A4 portrait


def test_repeated_header_fixture_has_several_pages(fixture_pdfs) -> None:
    assert inspect_pdf(fixture_pdfs["repeated_headers"]).page_count >= 2


@pytest.mark.parametrize("data", [b"", b"hello world", b"%PDF-1.7 truncated"])
def test_unreadable_pdf_is_rejected(data) -> None:
    with pytest.raises(AppError) as info:
        inspect_pdf(data)
    assert info.value.code is ErrorCode.INVALID_REQUEST


def test_oversized_pdf_is_rejected(fixture_pdfs, monkeypatch) -> None:
    monkeypatch.setattr(pdf_module, "MAX_PDF_BYTES", 100)
    with pytest.raises(AppError) as info:
        inspect_pdf(fixture_pdfs["adjacent_numeric_cells"])
    assert info.value.code is ErrorCode.DOCUMENT_TOO_LARGE


def test_base64_with_line_breaks_is_accepted(fixture_pdfs) -> None:
    pdf = fixture_pdfs["adjacent_numeric_cells"]
    wrapped = base64.encodebytes(pdf).decode()  # what the `base64` command prints: 76-column lines
    assert "\n" in wrapped
    assert ExtractionRequest.model_validate(_body(wrapped)).document.pdf_bytes == pdf


def _encrypted(fixture_pdf: bytes, user_password: str) -> bytes:
    writer = PdfWriter()
    writer.append(PdfReader(io.BytesIO(fixture_pdf)))
    writer.encrypt(user_password, owner_password="owner")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_locked_only_for_editing_is_accepted(fixture_pdfs) -> None:
    assert inspect_pdf(_encrypted(fixture_pdfs["adjacent_numeric_cells"], "")).page_count == 1


def test_password_protected_pdf_says_so(fixture_pdfs) -> None:
    with pytest.raises(AppError) as info:
        inspect_pdf(_encrypted(fixture_pdfs["adjacent_numeric_cells"], "secret"))
    assert info.value.code is ErrorCode.INVALID_REQUEST and "password" in info.value.message


def test_the_base64_text_is_not_kept_after_decoding(fixture_pdfs) -> None:
    request = ExtractionRequest.model_validate(_body(base64.b64encode(fixture_pdfs["adjacent_numeric_cells"]).decode()))
    assert request.document.content == "" and request.document.pdf_bytes


def test_the_data_uri_is_built_once(fixture_pdfs) -> None:
    pdf = inspect_pdf(fixture_pdfs["adjacent_numeric_cells"])
    assert pdf.data_uri is pdf.data_uri and pdf.data_uri.startswith("data:application/pdf;base64,")
