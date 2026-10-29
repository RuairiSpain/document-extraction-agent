"""GPT extraction and response assembly, with a fake model client."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from extraction.extractor import FUNCTION_TOOL_NAME, PrimaryExtractor, to_response
from models.errors import AppError, ErrorCode
from models.primary_output import PrimaryModelOutput, TableOutput
from models.request import PrimaryModel
from tests.conftest import adjacent_cells_output, cell, make_settings, pdf_from_bytes, table


class FakeResponses:
    def __init__(self, output: PrimaryModelOutput | None = None, *, error: Exception | None = None, delay: float = 0):
        self.output, self.error, self.delay = output, error, delay
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        if self.output is None:  # the model answered without calling the function
            return SimpleNamespace(status="completed", output=[])
        item = SimpleNamespace(type="function_call", name=FUNCTION_TOOL_NAME, arguments=self.output.model_dump_json())
        return SimpleNamespace(status="completed", output=[item])


def _extractor(responses: FakeResponses, **settings: Any) -> PrimaryExtractor:
    return PrimaryExtractor(SimpleNamespace(responses=responses), make_settings(**settings))


async def _extract(responses: FakeResponses, pdf_bytes: bytes, model=PrimaryModel.GPT_5_4_MINI, **settings):
    return await _extractor(responses, **settings).extract_document(pdf_from_bytes(pdf_bytes), model)


async def test_adjacent_cells_stay_separate_and_pages_carry_sizes(fixture_pdfs) -> None:
    result = await _extract(FakeResponses(adjacent_cells_output()), fixture_pdfs["adjacent_numeric_cells"])
    assert result.model_id == "gpt-5.4-mini"
    assert [c.content for c in result.tables[0].cells] == ["Product", "Quantity", "Rate", "A", "5", "8"]
    assert result.pages[0].page_number == 1 and 8 < result.pages[0].width < 8.5  # A4, in inches


async def test_table_crossing_pages_is_one_table(fixture_pdfs) -> None:
    cells = [cell(0, 0, "H", kind="columnHeader"), cell(1, 0, "x"), cell(2, 0, "y", page=2)]
    output = PrimaryModelOutput(paragraphs=[], tables=[table(3, 1, cells, pages=[1, 2])])
    result = await _extract(FakeResponses(output), fixture_pdfs["repeated_headers"])
    assert len(result.tables) == 1 and result.tables[0].page_numbers == [1, 2]


def test_response_json_uses_camel_case(fixture_pdfs) -> None:
    pdf = pdf_from_bytes(fixture_pdfs["adjacent_numeric_cells"])
    body = to_response(adjacent_cells_output(), pdf, "gpt-5.4-mini").model_dump(mode="json", by_alias=True)
    assert list(body)[:2] == ["modelId", "pages"]
    assert body["modelId"] == "gpt-5.4-mini" and body["tables"][0]["rowCount"] == 2
    assert body["tables"][0]["cells"][4]["columnIndex"] == 1 and body["evaluation"] is None


async def test_request_uses_forced_strict_function_and_the_right_deployment(fixture_pdfs) -> None:
    mini, pro = FakeResponses(adjacent_cells_output()), FakeResponses(adjacent_cells_output())
    await _extract(mini, fixture_pdfs["adjacent_numeric_cells"])
    await _extract(pro, fixture_pdfs["adjacent_numeric_cells"], PrimaryModel.GPT_5_4_PRO)
    assert mini.calls[0]["model"] == "deploy-mini" and pro.calls[0]["model"] == "deploy-pro"
    call = mini.calls[0]
    assert call["tool_choice"] == {"type": "function", "name": FUNCTION_TOOL_NAME}
    assert call["tools"][0]["strict"] is True and call["tools"][0]["name"] == FUNCTION_TOOL_NAME
    assert call["input"][0]["content"][0]["filename"] == "document.pdf"  # the caller's file name is never sent


def _status_error(cls, status: int):
    request = httpx.Request("POST", "https://x")
    return cls("boom", response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    "responses, code",
    [
        (FakeResponses(None), ErrorCode.EXTRACTION_FAILED),
        (
            FakeResponses(PrimaryModelOutput(paragraphs=[{"page_number": 9, "content": "x"}], tables=[])),
            ErrorCode.EXTRACTION_FAILED,
        ),
        (
            FakeResponses(error=openai.APIConnectionError(request=httpx.Request("POST", "https://x"))),
            ErrorCode.MODEL_UNAVAILABLE,
        ),
        (FakeResponses(delay=1), ErrorCode.EXTRACTION_TIMEOUT),
        (FakeResponses(error=_status_error(openai.BadRequestError, 400)), ErrorCode.EXTRACTION_REJECTED),
        (FakeResponses(error=_status_error(openai.RateLimitError, 429)), ErrorCode.MODEL_UNAVAILABLE),
        (FakeResponses(error=_status_error(openai.InternalServerError, 500)), ErrorCode.MODEL_UNAVAILABLE),
    ],
)
async def test_failures_map_to_error_codes(responses, code, fixture_pdfs) -> None:
    with pytest.raises(AppError) as info:
        await _extract(responses, fixture_pdfs["adjacent_numeric_cells"], extraction_timeout_seconds=0.05)
    assert info.value.code is code


def test_overlapping_cells_are_rejected() -> None:
    with pytest.raises(ValueError):
        table(2, 2, [cell(0, 0, "a", cs=2), cell(0, 1, "b")])


def test_pages_outside_the_document_are_rejected(fixture_pdfs) -> None:
    output = PrimaryModelOutput(paragraphs=[{"page_number": 2, "content": "x"}], tables=[])
    with pytest.raises(ValueError):
        to_response(output, pdf_from_bytes(fixture_pdfs["adjacent_numeric_cells"]), "gpt-5.4-mini")


def test_a_table_without_pages_or_cells_is_rejected() -> None:
    with pytest.raises(ValueError):
        table(1, 1, [], pages=[1])
    with pytest.raises(ValueError):
        TableOutput(page_numbers=[], row_count=1, column_count=1, cells=[cell(0, 0, "x")])


class SequenceResponses(FakeResponses):
    """Returns the given outputs in order (None means: the model did not call the function)."""

    def __init__(self, outputs: list[PrimaryModelOutput | None]) -> None:
        super().__init__()
        self.outputs = list(outputs)

    async def create(self, **kwargs: Any) -> Any:
        self.output = self.outputs.pop(0)
        return await super().create(**kwargs)


async def test_an_invalid_answer_is_retried_once(fixture_pdfs) -> None:
    responses = SequenceResponses([None, adjacent_cells_output()])
    result = await _extract(responses, fixture_pdfs["adjacent_numeric_cells"])
    assert len(responses.calls) == 2 and result.tables


async def test_two_invalid_answers_fail_after_two_calls(fixture_pdfs) -> None:
    responses = SequenceResponses([None, None])
    with pytest.raises(AppError) as info:
        await _extract(responses, fixture_pdfs["adjacent_numeric_cells"])
    assert info.value.code is ErrorCode.EXTRACTION_FAILED and len(responses.calls) == 2


async def test_outages_are_not_retried(fixture_pdfs) -> None:
    error = openai.APIConnectionError(request=httpx.Request("POST", "https://x"))
    responses = FakeResponses(error=error)
    with pytest.raises(AppError):
        await _extract(responses, fixture_pdfs["adjacent_numeric_cells"])
    assert len(responses.calls) == 1
