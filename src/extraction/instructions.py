"""Loads the version-controlled instructions for the GPT extraction.

The instructions forbid joining adjacent cells (``| 5 | 8 |`` must stay two cells, never ``5 8``).
Models tend to merge neighbouring numbers that look like one phrase, and that merge is the error
this service exists to avoid. The Mistral cross-check in ``comparison/`` looks for the same error.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_RESOURCE = Path(__file__).parent / "resources" / "primary_instructions.md"

USER_PROMPT = "Extract the complete attached PDF document according to the schema."


@lru_cache(maxsize=1)
def primary_instructions() -> str:
    return _RESOURCE.read_text(encoding="utf-8")
