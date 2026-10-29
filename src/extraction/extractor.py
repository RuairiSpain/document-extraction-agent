"""Primary extraction: the complete PDF in a single Responses API call.

The model is forced to answer through one strict function tool whose parameters are the
``PrimaryModelOutput`` schema. This works for every configured model, including those whose
published capabilities do not list structured outputs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Protocol

import openai
from pydantic import ValidationError

from common.pdf import PdfDocument
from config import Settings
from extraction.instructions import USER_PROMPT, primary_instructions
from models.errors import AppError, ErrorCode
from models.primary_output import PrimaryModelOutput
from models.request import PrimaryModel
from models.response import ExtractionResponse, PageSummary

logger = logging.getLogger(__name__)

FUNCTION_TOOL_NAME = "submit_document_extraction"
# HTTP statuses meaning "this input was refused". Others (401, 403, 404, 429, 5xx) are outages or setup problems.
# One retry when the model's answer is incomplete or structurally invalid.
MAX_ATTEMPTS = 2
REJECTED_STATUSES = frozenset({400, 409, 413, 422})


class ResponsesClient(Protocol):
    """The subset of ``openai.AsyncOpenAI`` used here (lets tests inject a fake)."""

    @property
    def responses(self) -> Any: ...


def build_input(pdf: PdfDocument) -> list[dict[str, Any]]:
    file_part: dict[str, Any] = {
        "type": "input_file",
        # A neutral filename: the caller's file name must not influence extraction.
        "filename": "document.pdf",
        "file_data": pdf.data_uri,
    }
    return [{"role": "user", "content": [file_part, {"type": "input_text", "text": USER_PROMPT}]}]


def function_tool() -> dict[str, Any]:
    tool = openai.pydantic_function_tool(
        PrimaryModelOutput,
        name=FUNCTION_TOOL_NAME,
        description="Submit the complete structured extraction of the attached PDF.",
    )
    # The SDK helper returns the Chat Completions shape; the Responses API wants the function flattened.
    return {"type": "function", **tool["function"]}


def _validation_failure(detail: str) -> AppError:
    logger.warning("primary output invalid: %s", detail)
    return AppError(
        ErrorCode.EXTRACTION_FAILED,
        "The primary extraction did not return valid structured output.",
    )


def to_response(output: PrimaryModelOutput, pdf: PdfDocument, model_id: str) -> ExtractionResponse:
    """Check the model's page numbers against the PDF, then attach the page sizes."""
    output.validate_pages(pdf.page_count)
    pages = [PageSummary(page_number=p.page_number, width=p.width_inches, height=p.height_inches) for p in pdf.pages]
    return ExtractionResponse(model_id=model_id, pages=pages, paragraphs=output.paragraphs, tables=output.tables)


class PrimaryExtractor:
    def __init__(self, client: ResponsesClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def extract_document(self, pdf: PdfDocument, model: PrimaryModel) -> ExtractionResponse:
        # Logical model -> deployment. No fallbacks, no escalation.
        s = self._settings
        deployment = s.extraction_model_pro if model is PrimaryModel.GPT_5_4_PRO else s.extraction_model_mini
        deadline = time.monotonic() + s.extraction_timeout_seconds
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return await self._attempt(pdf, model, deployment, deadline)
            except AppError as exc:
                # Only an invalid answer is worth another try. Outages, rejections and timeouts are not.
                if exc.code is not ErrorCode.EXTRACTION_FAILED or attempt == MAX_ATTEMPTS:
                    raise
                logger.warning("retrying after an invalid answer (attempt %d of %d)", attempt, MAX_ATTEMPTS)
        raise AssertionError("unreachable")

    async def _attempt(
        self, pdf: PdfDocument, model: PrimaryModel, deployment: str, deadline: float
    ) -> ExtractionResponse:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AppError(ErrorCode.EXTRACTION_TIMEOUT, "The primary extraction timed out.")
        try:
            output = await asyncio.wait_for(self._call(pdf, deployment, remaining), timeout=remaining)
        except (TimeoutError, openai.APITimeoutError):
            raise AppError(ErrorCode.EXTRACTION_TIMEOUT, "The primary extraction timed out.") from None
        except openai.APIStatusError as exc:
            logger.warning("primary model call failed: %s (status %s)", type(exc).__name__, exc.status_code)
            if exc.status_code in REJECTED_STATUSES:
                raise AppError(ErrorCode.EXTRACTION_REJECTED, "The extraction model rejected this document.") from None
            raise AppError(ErrorCode.MODEL_UNAVAILABLE, "The extraction model is unavailable.") from None
        except openai.APIConnectionError as exc:
            logger.warning("primary model call failed: %s", type(exc).__name__)
            raise AppError(ErrorCode.MODEL_UNAVAILABLE, "The extraction model is unavailable.") from None

        try:
            return to_response(output, pdf, model.value)
        except (ValidationError, ValueError) as exc:
            logger.warning("primary output failed validation: %s", type(exc).__name__)
            raise AppError(
                ErrorCode.EXTRACTION_FAILED, "The primary extraction did not pass structural validation."
            ) from None

    async def _call(self, pdf: PdfDocument, deployment: str, timeout: float) -> PrimaryModelOutput:
        response = await self._client.responses.create(
            model=deployment,
            instructions=primary_instructions(),
            input=build_input(pdf),
            tools=[function_tool()],
            tool_choice={"type": "function", "name": FUNCTION_TOOL_NAME},
            store=False,
            # Override the SDK's 600 s default read timeout. A non-streaming call sends nothing until
            # the model finishes, so a shorter HTTP timeout would cut off (and retry) a slow Pro call.
            # SDK retries for transient errors stay on the same deployment and within the overall
            # deadline enforced by asyncio.wait_for.
            timeout=timeout,
        )
        status = getattr(response, "status", None)
        if status not in (None, "completed"):
            raise _validation_failure(f"response status was '{status}'")
        for item in response.output or []:
            if getattr(item, "type", None) == "function_call" and getattr(item, "name", None) == FUNCTION_TOOL_NAME:
                try:
                    return PrimaryModelOutput.model_validate(json.loads(item.arguments))
                except (json.JSONDecodeError, ValidationError):
                    raise _validation_failure("function arguments did not match the schema") from None
        raise _validation_failure("the model did not call the extraction function")
