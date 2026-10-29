"""Pipeline and HTTP handler with fake models (no Azure)."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from extraction.extractor import to_response
from main import handle_extraction
from models.errors import AppError, ErrorCode, SecondaryError
from models.evaluation import ReasonCode
from models.request import ExtractionRequest
from pipeline import ExtractionPipeline
from tests.conftest import adjacent_cells_output, make_settings
from tests.unit.test_comparison import secondary_with


class FakePrimary:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def extract_document(self, pdf, model):
        if self.error:
            await asyncio.sleep(0.01)  # let the cross-check start first
            raise self.error
        return to_response(adjacent_cells_output(), pdf, model.value)


class FakeSecondary:
    model_name = "mistral-ocr-4-0"

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result, self.error, self.calls, self.cancelled = result, error, 0, False

    async def extract_secondary(self, pdf):
        self.calls += 1
        if self.error:
            raise self.error
        try:
            await asyncio.sleep(0 if self.result else 10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.result


GOOD = [["Product", "Quantity", "Rate"], ["A", "5", "8"]]


def _body(pdf: bytes, enabled: bool = True) -> dict:
    return {
        "document": {"content": base64.b64encode(pdf).decode(), "contentType": "application/pdf"},
        "evaluation": {"enabled": enabled},
    }


def _run(pipeline, pdf: bytes, enabled: bool = True):
    return pipeline.run(ExtractionRequest.model_validate(_body(pdf, enabled)))


async def test_evaluation_off_never_calls_the_cross_check(fixture_pdfs) -> None:
    secondary = FakeSecondary(secondary_with(GOOD))
    response = await _run(ExtractionPipeline(FakePrimary(), secondary), fixture_pdfs["adjacent_numeric_cells"], False)
    assert response.evaluation is None and secondary.calls == 0


async def test_evaluation_on_adds_agreement_without_changing_the_extraction(fixture_pdfs) -> None:
    pdf = fixture_pdfs["adjacent_numeric_cells"]
    with_eval = await _run(ExtractionPipeline(FakePrimary(), FakeSecondary(secondary_with(GOOD))), pdf)
    without = await _run(ExtractionPipeline(FakePrimary(), FakeSecondary()), pdf, False)
    assert with_eval.evaluation.document.agreement == 1.0
    assert with_eval.model_dump(exclude={"evaluation"}) == without.model_dump(exclude={"evaluation"})


async def test_cross_check_failure_never_fails_the_request(fixture_pdfs) -> None:
    error = SecondaryError(ReasonCode.SECONDARY_EXTRACTION_UNAVAILABLE, "down")
    response = await _run(
        ExtractionPipeline(FakePrimary(), FakeSecondary(error=error)), fixture_pdfs["adjacent_numeric_cells"]
    )
    assert response.tables and response.evaluation.document.agreement is None
    assert response.evaluation.document.reason is ReasonCode.SECONDARY_EXTRACTION_UNAVAILABLE


async def test_primary_failure_cancels_the_cross_check(fixture_pdfs) -> None:
    secondary = FakeSecondary()  # would sleep for 10 s
    pipeline = ExtractionPipeline(FakePrimary(AppError(ErrorCode.EXTRACTION_TIMEOUT, "slow")), secondary)
    with pytest.raises(AppError):
        await _run(pipeline, fixture_pdfs["adjacent_numeric_cells"])
    await asyncio.sleep(0)
    assert secondary.cancelled


def _client(pipeline) -> TestClient:
    async def endpoint(request):
        return await handle_extraction(request, pipeline)

    return TestClient(Starlette(routes=[Route("/invocations", endpoint, methods=["POST"])]))


def test_http_success_and_errors(fixture_pdfs) -> None:
    pdf = fixture_pdfs["adjacent_numeric_cells"]
    client = _client(ExtractionPipeline(FakePrimary(), FakeSecondary(secondary_with(GOOD))))
    ok = client.post("/invocations", json=_body(pdf))
    assert ok.status_code == 200 and ok.json()["evaluation"]["document"]["agreement"] == 1.0

    bad = client.post("/invocations", json={"document": {"content": "x!", "contentType": "application/pdf"}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_REQUEST"
    assert bad.json()["error"]["correlationId"]


def test_unexpected_errors_do_not_leak_details(fixture_pdfs) -> None:
    client = _client(ExtractionPipeline(FakePrimary(RuntimeError("secret internal detail")), FakeSecondary()))
    response = client.post("/invocations", json=_body(fixture_pdfs["adjacent_numeric_cells"], False))
    assert response.status_code == 500 and "secret" not in response.text


def test_log_level_applies_even_if_logging_is_already_configured(monkeypatch) -> None:
    import logging

    import main

    monkeypatch.setattr(main, "build_pipeline", lambda settings: (None, None))
    host = SimpleNamespace(invoke_handler=lambda f: f, shutdown_handler=lambda f: f)
    monkeypatch.setattr(main, "InvocationAgentServerHost", lambda: host)
    root = logging.getLogger()
    before = root.level
    try:
        logging.basicConfig()  # something else configured logging first
        main.create_app(make_settings(LOG_LEVEL="DEBUG"))
        assert root.level == logging.DEBUG
    finally:
        root.setLevel(before)
