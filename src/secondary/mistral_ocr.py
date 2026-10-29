"""Independent secondary extraction with Mistral OCR 4 on Microsoft Foundry.

The model receives only the original PDF. It never receives the primary extraction.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx
from azure.core.exceptions import ClientAuthenticationError

from common.pdf import PdfDocument
from config import Settings
from models.errors import SecondaryError
from models.evaluation import ReasonCode
from models.normalised_extraction import NormalisedExtraction
from secondary.normaliser import normalise

logger = logging.getLogger(__name__)

# Documented Foundry limits for mistral-ocr-4-0.
MAX_PAGES = 30
MAX_BYTES = 30 * 1024 * 1024
TIMEOUT_SECONDS = 300
# Scope for keyless Foundry model access.
TOKEN_SCOPE = "https://ai.azure.com/.default"


class AsyncTokenProvider(Protocol):
    async def get_token(self, *scopes: str, **kwargs: Any) -> Any: ...


def _unavailable(message: str) -> SecondaryError:
    return SecondaryError(ReasonCode.SECONDARY_EXTRACTION_UNAVAILABLE, message)


class MistralOcrClient:
    def __init__(self, settings: Settings, credential: AsyncTokenProvider, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._credential = credential
        self._http = http

    def build_body(self, pdf: PdfDocument) -> dict[str, Any]:
        return {
            "model": self._settings.secondary_extraction_model,
            "document": {"type": "document_url", "document_url": pdf.data_uri},
            "table_format": "html",
            "extract_header": True,
            "extract_footer": True,
            "include_image_base64": False,
        }

    async def ocr(self, pdf: PdfDocument) -> dict[str, Any]:
        try:
            token = await self._credential.get_token(TOKEN_SCOPE)
            response = await self._http.post(
                self._settings.mistral_ocr_endpoint(),
                json=self.build_body(pdf),
                headers={"Authorization": f"Bearer {token.token}"},
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            raise _unavailable("The secondary extraction timed out.") from None
        except (ClientAuthenticationError, httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning("secondary call failed: %s (status %s)", type(exc).__name__, status)
            raise _unavailable("The secondary model call failed.") from None
        if not isinstance(payload, dict):
            raise _unavailable("The secondary model returned an unexpected payload.")
        return payload


class OcrClient(Protocol):
    async def ocr(self, pdf: PdfDocument) -> dict[str, Any]: ...


class SecondaryExtractor:
    def __init__(self, client: OcrClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    @property
    def model_name(self) -> str:
        return self._settings.secondary_extraction_model

    def _check_limits(self, pdf: PdfDocument) -> None:
        if pdf.page_count > MAX_PAGES:
            raise SecondaryError(
                ReasonCode.SECONDARY_DOCUMENT_LIMIT_EXCEEDED,
                f"The document has {pdf.page_count} pages; the secondary model limit is {MAX_PAGES}.",
            )
        if pdf.size_bytes > MAX_BYTES:
            raise SecondaryError(
                ReasonCode.SECONDARY_DOCUMENT_LIMIT_EXCEEDED,
                f"The document is {pdf.size_bytes} bytes; the secondary model limit is {MAX_BYTES} bytes.",
            )

    async def extract_secondary(self, pdf: PdfDocument) -> NormalisedExtraction:
        self._check_limits(pdf)
        raw = await self._client.ocr(pdf)
        return normalise(raw, source_model=self.model_name, page_count=pdf.page_count)
