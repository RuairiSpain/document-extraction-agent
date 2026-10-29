"""Keeps the files in examples/ valid, so the documentation never drifts from the code."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from common.pdf import inspect_pdf
from models.request import ExtractionRequest
from models.response import ExtractionResponse

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_sample_pdf_is_valid() -> None:
    assert inspect_pdf((EXAMPLES / "sample.pdf").read_bytes()).page_count == 1


def test_request_example_matches_the_contract() -> None:
    request = ExtractionRequest.model_validate_json((EXAMPLES / "request.example.json").read_text())
    assert request.document.pdf_bytes == (EXAMPLES / "sample.pdf").read_bytes()
    assert request.evaluation.enabled


def test_response_example_matches_the_contract() -> None:
    response = ExtractionResponse.model_validate_json((EXAMPLES / "response.example.json").read_text())
    assert response.tables[0].cells[4].content == "5" and response.evaluation is not None


def test_client_builds_a_valid_request() -> None:
    spec = importlib.util.spec_from_file_location("call_agent", EXAMPLES / "call_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body = module.build_request(EXAMPLES / "sample.pdf", "gpt-5.4-pro", True)
    request = ExtractionRequest.model_validate(json.loads(json.dumps(body)))
    assert request.extraction.model.value == "gpt-5.4-pro" and request.evaluation.enabled
