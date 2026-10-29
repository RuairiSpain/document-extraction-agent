"""Foundry Hosted Agent entry point (Invocations protocol).

``POST /invocations`` with an ``ExtractionRequest`` JSON body returns an ``ExtractionResponse``
JSON body. Every invocation is independent: no session history is kept.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

import httpx
from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import DefaultAzureCredential
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from config import Settings, get_settings
from extraction.extractor import PrimaryExtractor
from models.errors import HTTP_STATUS, AppError, ErrorCode, ErrorDetail, ErrorResponse
from models.request import ExtractionRequest
from pipeline import ExtractionPipeline
from secondary.mistral_ocr import MistralOcrClient, SecondaryExtractor

logger = logging.getLogger("document_extraction_agent")


def error_response(code: ErrorCode, message: str, cid: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, correlation_id=cid))
    return JSONResponse(status_code=HTTP_STATUS[code], content=body.model_dump(mode="json", by_alias=True))


def _first_message(error: ValidationError) -> str:
    first = error.errors(include_url=False, include_input=False)[0]
    location = ".".join(str(part) for part in first["loc"]) or "body"
    return f"Invalid request at '{location}': {first['msg']}"


async def handle_extraction(request: Request, pipeline: ExtractionPipeline) -> JSONResponse:
    cid = getattr(request.state, "invocation_id", None) or str(uuid.uuid4())
    try:
        extraction_request = ExtractionRequest.model_validate_json(await request.body() or b"{}")
    except ValidationError as exc:
        logger.info("[%s] request rejected", cid)
        return error_response(ErrorCode.INVALID_REQUEST, _first_message(exc), cid)

    try:
        result = await pipeline.run(extraction_request)
    except AppError as exc:
        logger.info("[%s] request failed: %s", cid, exc.code.value)
        return error_response(exc.code, exc.message, cid)
    except Exception as exc:  # never leak internals to the caller
        logger.error("[%s] unexpected error: %s", cid, type(exc).__name__)
        return error_response(ErrorCode.INTERNAL_ERROR, "An unexpected internal error occurred.", cid)

    logger.info("[%s] request succeeded", cid)
    return JSONResponse(content=result.model_dump(mode="json", by_alias=True))


def build_pipeline(settings: Settings) -> tuple[ExtractionPipeline, Callable[[], Awaitable[None]]]:
    """Create the pipeline with Entra ID clients, and a ``close`` coroutine that releases them.

    ``DefaultAzureCredential`` uses the hosted agent's managed identity in Foundry and the
    developer's Azure CLI / azd login locally. No API keys are used.
    """
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=settings.foundry_project_endpoint, credential=credential)
    openai_client = project.get_openai_client()
    http = httpx.AsyncClient()
    pipeline = ExtractionPipeline(
        PrimaryExtractor(openai_client, settings),
        SecondaryExtractor(MistralOcrClient(settings, credential, http), settings),
    )

    async def close() -> None:
        await http.aclose()
        await openai_client.close()
        await project.close()
        await credential.close()

    return pipeline, close


def create_app(settings: Settings | None = None) -> InvocationAgentServerHost:
    settings = settings or get_settings()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger().setLevel(settings.log_level.upper())  # basicConfig does nothing if the host set up logging
    for noisy in ("httpx", "openai", "azure"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    pipeline, close = build_pipeline(settings)

    app = InvocationAgentServerHost()

    @app.invoke_handler
    async def invoke(request: Request) -> JSONResponse:
        return await handle_extraction(request, pipeline)

    @app.shutdown_handler
    async def shutdown() -> None:
        await close()

    return app


if __name__ == "__main__":
    create_app().run()
