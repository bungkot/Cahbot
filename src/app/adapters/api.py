"""FastAPI app factory + lifespan riil (pool asyncpg, worker, warm-up) + routers.

Komposisi manual: Container dibangun sekali di lifespan (`build_production_container`)
atau disuntik oleh ujian. Route HANYA menerjemahkan HTTP → use case (Clean
Architecture adapter), tanpa logika bisnis. Worker ingest berjalan in-process
(ADR D5) sebagai task lifespan — task dibatalkan rapi di shutdown.

Sprint 4: `/chat` streaming SSE (`stream=true`), sejarah `/sessions/{id}/messages`,
middleware contextvars (KR-5) — session_id + request_id terikat ke semua log.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated

import structlog
from fastapi import APIRouter, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.adapters.schemas import (
    ChatCitationsFrame,
    ChatFinalFrame,
    ChatRequest,
    ChatResponse,
    ChatTokenFrame,
    CitationOut,
    HealthResponse,
    IngestJobOut,
    IngestJobsResponse,
    IngestResponse,
    SessionMessageOut,
    SessionMessagesResponse,
)
from app.adapters.worker.watchdog_runner import InboxWatchdogRunner
from app.core.config import Settings, get_settings
from app.core.container import Container, build_production_container
from app.core.logging import configure_logging
from app.domain.chat import (
    ChatCitationsEvent,
    ChatFinalEvent,
    ChatTokenEvent,
    Citation,
)
from app.domain.documents import IngestJob as DomainIngestJob
from app.domain.errors import IngestError, RetrievalError

logger = structlog.get_logger(__name__)

STATE_REQUEST_ATTR = "container"
STATE_WORKER_ATTR = "worker_task"
SSE_MEDIA_TYPE = "text/event-stream"


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
            # Lifespan riil: pool Postgres + repo + parser + embedder + LLM (Sprint 4).
            try:
                live_container = await build_production_container(config)
            except Exception as exc:  # DB belum siap — app degraded, routes 503
                logger.error("container_build_failed", error=str(exc))
        if live_container is not None:
            app.state.__dict__[STATE_REQUEST_ATTR] = live_container
            await _warm_up_models(live_container, config)
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
    _register_context_middleware(app)
    _register_routes(app, config)
    return app


async def _warm_up_models(live_container: Container, config: Settings) -> None:
    """Warm-up embedder (Sprint 2) + LLM gateway (Sprint 4 KR-1) — sekali di startup."""

    try:
        await live_container.embedder.warm_up()
        logger.info("embedder_warmup_done", model=live_container.embedder.model_name)
    except Exception as exc:
        # App tetap hidup; POST /ingest akan 502 sampai model/tokenizer siap.
        logger.warning("embedder_warmup_failed", error=str(exc))

    try:
        await live_container.llm.warm_up()
        logger.info("llm_warmup_done", model=live_container.llm.model_name)
    except Exception as exc:
        # Cadangan runtime: placeholder/composisi sudah di-log; chat tetap tak halusinasi.
        logger.warning("llm_warmup_failed", model_path=str(config.model_path), error=str(exc))


def _register_context_middleware(app: FastAPI) -> None:
    """KR-5: clear contextvars per-permintaan + request_id baru — koneksi
    keep-alive tidak mewarisi konteks tinggal (atribusi log kekal tepat)."""

    @app.middleware("http")
    async def _request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=uuid.uuid4().hex[:12])
        try:
            return await call_next(request)
        finally:
            # session_id (di-bind route /chat) ikut dibersihkan di sini.
            structlog.contextvars.clear_contextvars()


def _register_routes(app: FastAPI, config: Settings) -> None:
    """Semua route HTTP — tanpa logika bisnis; error domain → HTTP status."""

    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        model_ok = Path(config.model_path).exists()
        return HealthResponse(
            status="ok" if model_ok else "degraded",
            database="unknown",  # ping penuh oleh pool — forward Sprint 6
            model_file="ok" if model_ok else "missing",
        )

    @router.post("/chat", response_model=None)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse | StreamingResponse:
        """Tanya-jawab grounded. `stream=false` → JSON; `stream=true` → SSE aliran."""
        state = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        if state is None:
            raise HTTPException(status_code=503, detail="layanan belum siap")
        session_id = payload.session_id or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(session_id=session_id)

        try:
            if payload.stream:
                return _chat_stream_response(state, payload, session_id)
            answer = await state.chat.execute(
                payload.question, session_id, history_override=payload.history
            )
        except RetrievalError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return ChatResponse(
            content=answer.content,
            citations=[_citation_out(c) for c in answer.citations],
            refusal=answer.refusal,
            is_refusal=answer.is_refusal,
        )

    @router.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
    async def session_messages(
        session_id: str, request: Request, limit: int = 30
    ) -> SessionMessagesResponse:
        """KR-3: sejarah sesi mengikut masa menaik (maks 50 mesej)."""

        state = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        if state is None:
            raise HTTPException(status_code=503, detail="layanan belum siap")
        try:
            uuid.UUID(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="session_id bukan uuid") from exc

        bounded = max(1, min(limit, 50))
        messages = await state.history.recent_messages(session_id, bounded)
        return SessionMessagesResponse(
            session_id=session_id,
            messages=[
                SessionMessageOut(role=m.role, content=m.content, created_at=m.created_at)
                for m in messages
            ],
        )

    @router.post("/ingest", response_model=IngestResponse)
    async def ingest(request: Request, file: Annotated[UploadFile, File()]) -> IngestResponse:
        """POST serentak untuk MVP (KR-4): jalankan parse→chunk→embed→upsert."""

        state = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        if state is None:
            raise HTTPException(status_code=503, detail="layanan belum siap")
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
            raise HTTPException(status_code=503, detail="layanan belum siap")
        jobs = await state.ingest_jobs.list_jobs(limit=max(1, min(limit, 200)))
        return IngestJobsResponse(jobs=[_job_out(j) for j in jobs])

    @router.get("/ingest/jobs/{job_id}")
    async def ingest_job(job_id: int, request: Request) -> IngestJobOut:
        state = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        if state is None:
            raise HTTPException(status_code=503, detail="layanan belum siap")
        job = await state.ingest_jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job tidak dijumpai")
        return _job_out(job)

    app.include_router(router)


def _chat_stream_response(
    state: Container,
    payload: ChatRequest,
    session_id: str,
) -> StreamingResponse:
    """SSE: event `citations` → `token`* → `final`. Ralat retrieval → event `error`."""

    async def sse() -> AsyncIterator[str]:
        try:
            async for event in state.chat.execute_stream(
                payload.question, session_id, history_override=payload.history
            ):
                if isinstance(event, ChatCitationsEvent):
                    frame = ChatCitationsFrame(
                        citations=[_citation_out(c) for c in event.citations]
                    )
                    yield _sse_frame("citations", frame)
                elif isinstance(event, ChatTokenEvent):
                    yield _sse_frame("token", ChatTokenFrame(text=event.text))
                elif isinstance(event, ChatFinalEvent):
                    answer = event.answer
                    yield _sse_frame(
                        "final",
                        ChatFinalFrame(
                            content=answer.content,
                            citations=[_citation_out(c) for c in answer.citations],
                            refusal=answer.refusal,
                            is_refusal=answer.is_refusal,
                        ),
                    )
        except RetrievalError as exc:
            yield _sse_frame("error", {"detail": str(exc)})
        except Exception:
            # Catch-all: stream yang sudah separuh jalan mesti DITUTUP dengan
            # bingkai error — jangan sampai klien mendekati aliran terpotong
            # tanpa sebab (detail generik; jejak penuh di log server).
            logger.exception("chat_stream_failed")
            yield _sse_frame("error", {"detail": "kegagalan pelayan semasa menjawab"})

    return StreamingResponse(sse(), media_type=SSE_MEDIA_TYPE)


def _sse_frame(event: str, payload: object) -> str:
    """Bingkai SSE: `event: <nama>` + `data: <json>`.

    Model Pydantic di-flatten `model_dump()` — tanpa itu json.dumps mengubah
    frame menjadi repr-string bundel (data ter-encode ganda, rosak untuk UI).
    """

    data = payload.model_dump() if isinstance(payload, BaseModel) else payload
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


def _citation_out(citation: Citation) -> CitationOut:
    """Domain Citation → schema adapters (tanpa logika bisnis)."""

    return CitationOut(
        source=citation.source,
        chunk_index=citation.chunk_index,
        snippet=citation.snippet,
    )


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
