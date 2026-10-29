"""Public request contract: ``ExtractionRequest``."""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator
from pydantic.alias_generators import to_camel


class PrimaryModel(StrEnum):
    """Logical GPT models. Physical deployment names come from configuration."""

    GPT_5_4_MINI = "gpt-5.4-mini"
    GPT_5_4_PRO = "gpt-5.4-pro"


class _RequestModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class DocumentInput(_RequestModel):
    content: str = Field(description="Base64-encoded PDF bytes.")
    content_type: str = Field(description="Only application/pdf is accepted.")
    file_name: str | None = Field(default=None, description="Not used. Never influences extraction.")

    _pdf_bytes: bytes = PrivateAttr(default=b"")

    @field_validator("content_type")
    @classmethod
    def _check_content_type(cls, value: str) -> str:
        if value.strip().lower() != "application/pdf":
            raise ValueError("Unsupported content type. Only application/pdf is accepted.")
        return value

    @model_validator(mode="after")
    def _decode(self) -> DocumentInput:
        try:
            # Tools such as the `base64` command wrap lines, so whitespace is ignored.
            self._pdf_bytes = base64.b64decode("".join(self.content.split()), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("document.content is not valid Base64.") from None
        self.content = ""  # the decoded bytes are kept instead; do not hold a second copy of a large PDF
        return self

    @property
    def pdf_bytes(self) -> bytes:
        return self._pdf_bytes


class ExtractionOptions(_RequestModel):
    model: PrimaryModel = PrimaryModel.GPT_5_4_MINI


class EvaluationOptions(_RequestModel):
    enabled: bool = False


class ExtractionRequest(_RequestModel):
    document: DocumentInput
    extraction: ExtractionOptions = Field(default_factory=ExtractionOptions)
    evaluation: EvaluationOptions = Field(default_factory=EvaluationOptions)
