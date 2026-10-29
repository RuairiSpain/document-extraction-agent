"""Live integration tests. Run explicitly with ``pytest -m integration``.

Requires ``az login`` (or another DefaultAzureCredential source) and, in the environment or
``src/.env``: FOUNDRY_PROJECT_ENDPOINT, EXTRACTION_MODEL_MINI, EXTRACTION_MODEL_PRO and
SECONDARY_EXTRACTION_MODEL. Only synthetic fixtures are sent.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from config import Settings
from main import build_pipeline

ENV_FILE = Path(__file__).resolve().parents[2] / "src" / ".env"


@pytest.fixture
def live_settings() -> Settings:
    try:
        return Settings(_env_file=ENV_FILE if ENV_FILE.exists() else None)  # type: ignore[call-arg]
    except ValidationError:
        pytest.skip("Live Foundry configuration not set (see .env.example).")


@pytest.fixture
async def live(live_settings):
    pipeline, close = build_pipeline(live_settings)
    try:
        yield pipeline
    finally:
        await close()


def run_pro() -> bool:
    return os.environ.get("RUN_PRO_INTEGRATION", "").lower() in ("1", "true", "yes")
