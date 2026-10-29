"""Public response contract: ``ExtractionResponse``."""

from __future__ import annotations

from models.evaluation import EvaluationResult
from models.primary_output import ParagraphOutput, TableOutput, _Strict


class PageSummary(_Strict):
    page_number: int
    width: float  # inches
    height: float  # inches


class ExtractionResponse(_Strict):
    """The GPT extraction plus a separate, optional ``evaluation``.

    Removing ``evaluation`` leaves exactly the GPT extraction. The fields repeat those of
    ``PrimaryModelOutput`` so that ``modelId`` and ``pages`` come first in the JSON.
    """

    model_id: str
    pages: list[PageSummary]
    paragraphs: list[ParagraphOutput]
    tables: list[TableOutput]
    evaluation: EvaluationResult | None = None
