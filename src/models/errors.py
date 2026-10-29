"""Application error model. Messages are safe to return to callers."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from models.evaluation import ReasonCode


class ErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"  # bad JSON, Base64, content type, model name or PDF
    DOCUMENT_TOO_LARGE = "DOCUMENT_TOO_LARGE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"  # GPT call failed: network, auth, deployment, throttling, outage
    EXTRACTION_REJECTED = "EXTRACTION_REJECTED"  # the model refused this input; retrying will not help
    EXTRACTION_TIMEOUT = "EXTRACTION_TIMEOUT"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"  # the model answered, but not with a valid extraction
    INTERNAL_ERROR = "INTERNAL_ERROR"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.DOCUMENT_TOO_LARGE: 413,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.EXTRACTION_REJECTED: 502,
    ErrorCode.EXTRACTION_TIMEOUT: 504,
    ErrorCode.EXTRACTION_FAILED: 502,
    ErrorCode.INTERNAL_ERROR: 500,
}


class AppError(Exception):
    """Raised for any failure that ends the request. ``message`` must be caller-safe."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SecondaryError(Exception):
    """Raised by the secondary path. Never ends the request; becomes an evaluation warning.

    ``code`` is the reason reported in ``evaluation.document.reason``. ``message`` must be caller-safe.
    """

    def __init__(self, code: ReasonCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ErrorDetail(_CamelModel):
    code: ErrorCode
    message: str
    correlation_id: str


class ErrorResponse(_CamelModel):
    error: ErrorDetail
