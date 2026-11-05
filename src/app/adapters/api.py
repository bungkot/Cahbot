"""FastAPI app factory + lifespan (pool asyncpg, model warm-up) + router `/chat`.

Komposisi manual: Container dibangun sekali di lifespan; route hanya menerjemahkan
HTTP → use case (Clean Architecture adapter), tanpa logika bisnis.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import APIRouter, FastAPI, HTTPException, Request, status

from app.adapters.schemas import ChatRequest, ChatResponse, CitationOut, HealthResponse
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.errors import RetrievalError

logger = structlog.get_logger(__name__)

STATE_REQUEST_ATTR = "container"


def create_app(
    settings: Settings | None = None,
    *,
    container: object | None = None,  # Container (real) — dipasang oleh lifespan/pengujian
) -> FastAPI:
    """Factory aplikasi. `container` meng-inject generik — jaga signature stabil."""

    config = settings or get_settings()
    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("app_start", model=config.model_path, db=config.database_url.split("@")[-1])
        if container is None:
            logger.warning("container_not_set", hint="lifespan real akan dibuat sprint infra")
        else:
            app.state.__dict__[STATE_REQUEST_ATTR] = container
        yield
        logger.info("app_stop")

    app = FastAPI(title="rag-mvp", version="0.1.0", lifespan=lifespan)

    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        model_ok = Path(config.model_path).exists()
        return HealthResponse(
            status="ok" if model_ok else "degraded",
            database="unknown",  # diperiksa penuh oleh adapters/pool pada sprint infra
            model_file="ok" if model_ok else "missing",
        )

    @router.post("/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        use_case = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        if use_case is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="layanan belum siap",
            )
        try:
            answer = await use_case.chat.execute(payload.question, payload.history)
        except RetrievalError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return ChatResponse(
            content=answer.content,
            citations=[
                CitationOut(source=c.source, chunk_index=c.chunk_index, snippet=c.snippet)
                for c in answer.citations
            ],
            refusal=answer.refusal,
            is_refusal=answer.is_refusal,
        )

    app.include_router(router)
    return app


app = create_app()
