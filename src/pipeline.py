"""Orchestration: validate -> primary extraction (with the independent evaluation running alongside) -> response."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

from common.pdf import PdfDocument, inspect_pdf
from comparison.comparator import compare_extractions
from models.errors import SecondaryError
from models.evaluation import DocumentEvaluation, EvaluationResult, EvaluationWarning, ReasonCode
from models.normalised_extraction import NormalisedExtraction
from models.request import ExtractionRequest, PrimaryModel
from models.response import ExtractionResponse

logger = logging.getLogger(__name__)

_UNAVAILABLE = "Primary extraction completed successfully but independent evaluation was unavailable."


class PrimaryExtraction(Protocol):
    async def extract_document(self, pdf: PdfDocument, model: PrimaryModel) -> ExtractionResponse: ...


class SecondaryExtraction(Protocol):
    @property
    def model_name(self) -> str: ...

    async def extract_secondary(self, pdf: PdfDocument) -> NormalisedExtraction: ...


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _unavailable(secondary_model: str, reason: ReasonCode, message: str | None = None) -> EvaluationResult:
    return EvaluationResult(
        secondary_model=secondary_model,
        document=DocumentEvaluation(agreement=None, reason=reason),
        warnings=[
            EvaluationWarning(code=reason.value, message=f"{_UNAVAILABLE} {message}" if message else _UNAVAILABLE)
        ],
    )


def _consume_exception(task: asyncio.Task) -> None:
    """Mark a finished task's exception as retrieved, so an abandoned secondary task never logs noise."""
    if not task.cancelled():
        task.exception()


class ExtractionPipeline:
    def __init__(self, primary: PrimaryExtraction, secondary: SecondaryExtraction) -> None:
        self._primary = primary
        self._secondary = secondary

    async def run(self, request: ExtractionRequest) -> ExtractionResponse:
        # PDF parsing is CPU-bound; keep it off the event loop so other requests are not stalled.
        pdf = await asyncio.to_thread(inspect_pdf, request.document.pdf_bytes)
        model = request.extraction.model
        logger.info(
            "extraction started: model=%s evaluation=%s bytes=%d pages=%d",
            model.value,
            request.evaluation.enabled,
            pdf.size_bytes,
            pdf.page_count,
        )

        # The secondary extraction only needs the PDF, so it runs concurrently with the primary one.
        secondary: asyncio.Task[NormalisedExtraction] | None = None
        if request.evaluation.enabled:
            secondary = asyncio.create_task(self._secondary.extract_secondary(pdf))
            secondary.add_done_callback(_consume_exception)
        try:
            primary = await self._extract_primary(pdf, model)
        except BaseException:
            if secondary:
                secondary.cancel()
            raise

        evaluation = await self._evaluate(primary, secondary) if secondary else None
        return primary.model_copy(update={"evaluation": evaluation}) if evaluation else primary

    async def _extract_primary(self, pdf: PdfDocument, model: PrimaryModel) -> ExtractionResponse:
        start = time.perf_counter()
        try:
            primary = await self._primary.extract_document(pdf, model)
        except Exception as exc:
            logger.warning(
                "primary extraction failed after %d ms: %s", _elapsed_ms(start), getattr(exc, "code", "INTERNAL_ERROR")
            )
            raise
        logger.info(
            "primary extraction completed in %d ms: %d table(s), %d page(s)",
            _elapsed_ms(start),
            len(primary.tables),
            len(primary.pages),
        )
        return primary

    async def _evaluate(
        self, primary: ExtractionResponse, secondary_task: asyncio.Task[NormalisedExtraction]
    ) -> EvaluationResult:
        name = self._secondary.model_name
        try:
            secondary = await secondary_task
        except SecondaryError as exc:
            logger.warning("secondary extraction unavailable: %s", exc.code.value)
            return _unavailable(name, exc.code, exc.message)
        except Exception as exc:
            logger.warning("secondary extraction failed unexpectedly: %s", type(exc).__name__)
            return _unavailable(name, ReasonCode.SECONDARY_EXTRACTION_UNAVAILABLE)

        try:
            evaluation = compare_extractions(primary, secondary)
        except Exception as exc:
            logger.warning("comparison failed: %s", type(exc).__name__)
            return _unavailable(name, ReasonCode.COMPARISON_FAILED, "The extraction comparison failed.")
        if secondary.unsupported_elements:
            evaluation.warnings.append(
                EvaluationWarning(
                    code="SECONDARY_ELEMENTS_NOT_COMPARED",
                    message="Not compared: " + ", ".join(secondary.unsupported_elements) + ".",
                )
            )
        logger.info(
            "comparison completed: %d secondary table(s), agreement %s",
            len(secondary.tables),
            evaluation.document.agreement,
        )
        return evaluation
