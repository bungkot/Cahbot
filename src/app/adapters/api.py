"""FastAPI app factory + lifespan riil (pool asyncpg, tugas worker, warm-up) + routers.

Komposisi manual: Container dibangun sekali di lifespan (`build_production_container`)
atau disuntik oleh pengujian. Route HANYA menerjemahkan HTTP → use case (Clean
Architecture adapter), tanpa logika bisnis. Worker ingest berjalan in-process
(ADR D5) sebagai task lifespan — task dibatalkan rapi di shutdown.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated

import structlog
from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile, status

from app.adapters.schemas import (
    ChatRequest,
    ChatResponse,
    CitationOut,
    HealthResponse,
    IngestJobOut,
    IngestJobsResponse,
    IngestResponse,
)
from app.adapters.worker.watchdog_runner import InboxWatchdogRunner
from app.core.config import Settings, get_settings
from app.core.container import Container, build_production_container
from app.core.logging import configure_logging
from app.domain.documents import IngestJob as DomainIngestJob
from app.domain.errors import IngestError, RetrievalError
from app.infrastructure.placeholder_llm import PlaceholderLLMGateway

logger = structlog.get_logger(__name__)

STATE_REQUEST_ATTR = "container"
STATE_WORKER_ATTR = "worker_task"


def create_app(
    settings: Settings | None = None,
    *,
    container: Container | None = None,
    start_worker: bool = True,
) -> FastAPI:
    """Factory aplikasi. `container` di-inject pembangun real/pengujian."""

    config = settings or get_settings()
    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("app_start", model=config.model_path, db=config.database_url.split("@")[-1])
        live_container = container
        if live_container is None:
            # Lifespan riil: pool Postgres + repo + parser + embedder (Sprint 2).
            try:
                live_container = await build_production_container(
                    config, llm=PlaceholderLLMGateway()
                )
            except Exception as exc:  # DB belum siap — app degraded, routes 503
                logger.error("container_build_failed", error=str(exc))
        if live_container is not None:
            app.state.__dict__[STATE_REQUEST_ATTR] = live_container
            await _warm_up_embedder(live_container)
        worker_task: asyncio.Task[None] | None = None
        if start_worker and live_container is not None:
            worker = InboxWatchdogRunner(
                ingest=live_container.ingest,
                job_store=live_container.ingest_jobs,
                inbox_root=config.ingest_inbox_path,
                poll_interval_s=config.ingest_poll_interval_s,
                max_attempts=config.ingest_max_attempts,
            )
            worker_task = asyncio.create_task(worker.run(), name="ingest-worker")
            app.state.__dict__[STATE_WORKER_ATTR] = worker_task
        yield
        if worker_task is not None:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                logger.info("worker_stopped")
        logger.info("app_stop")

    app = FastAPI(title="rag-mvp", version="0.1.0", lifespan=lifespan)
    _register_routes(app, config)
    return app


async def _warm_up_embedder(containable: Container) -> None:
    """KR-2: load + verifikasi dimensi 384 sekali di startup (golden rule #3)."""

    try:
        await containable.embedder.warm_up()
        logger.info("embedder_warmup_done", model=containable.embedder.model_name)
    except Exception as exc:
        # App tetap hidup; POST /ingest akan 502 sampai model/tokenizer siap.
        logger.warning("embedder_warmup_failed", error=str(exc))


def _register_routes(app: FastAPI, config: Settings) -> None:
    """Semua route HTTP — tanpa logika bisnis; error domain → HTTP status."""

    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        model_ok = Path(config.model_path).exists()
        return HealthResponse(
            status="ok" if model_ok else "degraded",
            database="unknown",  # pemeriksaan penuh oleh pool di Sprint 3+
            model_file="ok" if model_ok else "missing",
        )

    @router.post("/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        state = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        if state is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="layanan belum siap",
            )
        try:
            answer = await state.chat.execute(payload.question, payload.history)
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

    @router.post("/ingest", response_model=IngestResponse)
    async def ingest(request: Request, file: Annotated[UploadFile, File()]) -> IngestResponse:
        """POST serentak untuk MVP (KR-4): jalankan parse→chunk→embed→upsert."""

        state = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        if state is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="layanan belum siap",
            )
        data = await file.read()
        if not data:
            raise HTTPException(status_code=422, detail="fail kosong — tiada kandungan")
        source = _sanitize_filename(file.filename)
        try:
            result = await state.ingest.execute(data, source=source)
        except IngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return IngestResponse(
            source=result.source,
            status="done",
            n_chunks=result.n_chunks,
            content_hash=result.content_hash,
        )

    @router.get("/ingest/jobs", response_model=IngestJobsResponse)
    async def ingest_jobs(request: Request, limit: int = 50) -> IngestJobsResponse:
        state = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        if state is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="layanan belum siap",
            )
        jobs = await state.ingest_jobs.list_jobs(limit=max(1, min(limit, 200)))
        return IngestJobsResponse(jobs=[_job_out(j) for j in jobs])

    @router.get("/ingest/jobs/{job_id}")
    async def ingest_job(job_id: int, request: Request) -> IngestJobOut:
        state = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        if state is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="layanan belum siap",
            )
        job = await state.ingest_jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job tidak dijumpai")
        return _job_out(job)

    app.include_router(router)


def _job_out(job: DomainIngestJob) -> IngestJobOut:
    """Penerjemahan domain → schema (adapters) — tanpa logika bisnis."""

    return IngestJobOut(
        id=job.id,
        path=job.path,
        status=str(job.status),
        attempts=job.attempts,
        error=job.error,
    )


def _sanitize_filename(raw: str | None) -> str:
    """Nama file unggahan → basename POSIX aman (idem worker path format)."""

    fallback = "tanpa-nama.bin"
    if not raw:
        return fallback
    name = PurePosixPath(raw.replace("\\", "/")).name
    return name.strip() or fallback


app = create_app()
