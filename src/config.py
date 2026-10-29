"""Typed application settings, read from environment variables (and a local ``.env``)."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # FOUNDRY_PROJECT_ENDPOINT is injected by the hosting platform.
    foundry_project_endpoint: str = Field(validation_alias="FOUNDRY_PROJECT_ENDPOINT")
    extraction_model_mini: str = Field(validation_alias="EXTRACTION_MODEL_MINI")
    extraction_model_pro: str = Field(validation_alias="EXTRACTION_MODEL_PRO")
    secondary_extraction_model: str = Field(default="mistral-ocr-4-0", validation_alias="SECONDARY_EXTRACTION_MODEL")
    extraction_timeout_seconds: float = Field(default=1800, gt=0, validation_alias="EXTRACTION_TIMEOUT_SECONDS")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    def mistral_ocr_endpoint(self) -> str:
        """``https://<account>.services.ai.azure.com/providers/mistral/azure/ocr``."""
        parts = urlsplit(self.foundry_project_endpoint)
        return f"{parts.scheme}://{parts.netloc}/providers/mistral/azure/ocr"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
