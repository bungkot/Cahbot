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
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.adapters.chainlit_state import set_container
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
from app.adapters.security import ApiKeyGuard, SessionRateLimiter
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

# Sprint 6 KR-2: had waktu langkah shutdown (drain pool) — shutdown mesti
# selesai segera; tak selesai → warning dan proses keluar tetap.
SHUTDOWN_DRAIN_TIMEOUT_S = 10.0

_REQUEUE_BOOT_REASON = "job `running` tertinggal — proses lama mati; dipulihkan saat startup"


def create_app(
    settings: Settings | None = None,
    *,
    container: Container | None = None,
    start_worker: bool = True,
) -> FastAPI:
    """Factory aplikasi. `container` di-inject pembangun real/pengujian."""

    config = settings or get_settings()
    configure_logging(config.log_level)
    limiter, rate_dep = _deps_rate_limit(config)

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
            set_container(live_container)  # UI Chainlit (sub-ASGI) membaca di sini
            await _warm_up_models(live_container, config)
        # Sprint 6 KR-2 boot-resume: job `running` tertinggal proses mati —
        # await (bukan fire-and-forget) agar beres SEBELUM worker memetik.
        if live_container is not None:
            await _requeue_stuck_jobs(live_container)
        trim_task: asyncio.Task[None] | None = None
        if limiter.enabled:
            trim_task = _start_trim_task(limiter)
        worker_task: asyncio.Task[None] | None = None
        if start_worker and live_container is not None:
            worker = InboxWatchdogRunner(
                ingest=live_container.ingest,
                job_store=live_container.ingest_jobs,
                inbox_root=config.ingest_inbox_path,
                poll_interval_s=config.ingest_poll_interval_s,
                max_attempts=config.ingest_max_attempts,
                rescan_interval_s=config.ingest_rescan_interval_s,
            )
            worker_task = asyncio.create_task(worker.run(), name="ingest-worker")
            app.state.__dict__[STATE_WORKER_ATTR] = worker_task
        yield
        # --- graceful shutdown (Sprint 6 KR-2): stop worker → drain pool ---
        if trim_task is not None:
            trim_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await trim_task
        if worker_task is not None:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                logger.info("worker_stopped")
            except Exception as exc:
                # Worker mati dengan ralat sebenar — jangan skip drain pool.
                logger.error("worker_task_failed", error=str(exc))
        if live_container is not None and live_container.pool_closer is not None:
            try:
                await asyncio.wait_for(
                    live_container.pool_closer(), timeout=SHUTDOWN_DRAIN_TIMEOUT_S
                )
            except TimeoutError:
                logger.warning("pool_drain_timeout", timeout_s=SHUTDOWN_DRAIN_TIMEOUT_S)
            except Exception as exc:
                logger.warning("pool_drain_failed", error=str(exc))
        logger.info("app_stop")

    app = FastAPI(title="rag-mvp", version="0.1.0", lifespan=lifespan)
    _register_context_middleware(app)
    _register_routes(app, config, rate_handler=rate_dep)
    _mount_chainlit_ui(app)  # UI "/" (di-mount TERAKHIR — /api routes kekal utama)
    return app


def _deps_guard(
    config: Settings,
) -> Callable[[Request], Awaitable[None]]:
    """Dependency API key — riil bila `API_KEY` diisi, no-op bila kosong."""

    guard = ApiKeyGuard(config)

    async def _dependency(request: Request) -> None:
        guard.check(request)

    return _dependency


def _deps_rate_limit(
    config: Settings,
) -> tuple[SessionRateLimiter, Callable[[Request], Awaitable[None]]]:
    """Dependency had kadar per-klien: kunci dasar IP; session_id (badan
    /chat) dijadikan gadai kedua (rotasi sid tidak mengelak had)."""

    limiter = SessionRateLimiter(config)

    async def _dependency(request: Request) -> None:
        client_ip = request.client.host if request.client else "tanpa-alamat"
        key = client_ip
        if request.url.path == "/chat":
            # Badan dibaca (di-cache Starlette) — Pydantic membaca semula dari
            # cache yang sama. Gagal parse → tetap dihad dengan kunci IP sahaja.
            raw = await request.body()
            try:
                sid = str(json.loads(raw).get("session_id") or "")
                if sid:
                    key = f"{client_ip}|sid:{sid}"
            except (ValueError, AttributeError):
                pass
        limiter.check(key)

    return limiter, _dependency


def _start_trim_task(limiter: SessionRateLimiter) -> asyncio.Task[None]:
    """Pembersihan berkala peta kad (review Sprint 6): buang kunci lapuk
    setiap 5 minit — memori dijaga tanpa mengganggu request hot path."""

    async def _loop() -> None:
        while True:
            await asyncio.sleep(300.0)
            removed = limiter.trim_stale_sessions()
            if removed:
                logger.info("rate_keys_trimmed", removed=removed)

    return asyncio.create_task(_loop(), name="rate-trim")


async def _requeue_stuck_jobs(live_container: Container) -> None:
    """Sprint 6 KR-2: job `running` dari proses mati → `pending` (boot-resume).

    Best-effort: kegagalan DB hanya di-log — app tetap hidup (job boleh
    ditaruh semula manual lewat POST /ingest).
    """

    try:
        await live_container.ingest_jobs.requeue_stuck(_REQUEUE_BOOT_REASON)
    except Exception as exc:
        logger.warning("requeue_stuck_failed", error=str(exc))


def _mount_chainlit_ui(app: FastAPI) -> None:
    """Sprint 5 KR-1: Chainlit sub-ASGI di "/" (D6-update, theming via swiss.css).

    Target module TIDAK di-import di sini (mount_chainlit memuatkan module
    sendiri; import ganda mendaftar callback dua kali). Laluan fail dihitung
    dari spec pakej — konsisten dev (src-layout) dan image prod (site-packages).
    """

    try:
        import importlib.util

        from chainlit.utils import mount_chainlit

        spec = importlib.util.find_spec("app.adapters.chainlit_app")
        origin = getattr(spec, "origin", None)
        if origin is None:
            logger.warning("chainlit_target_missing")
            return
        mount_chainlit(app, target=origin, path="/")
        logger.info("chainlit_mounted", path="/")
    except Exception as exc:  # chainlit gagal muat → API tetap hidup (degraded UI)
        logger.warning("chainlit_mount_failed", error=str(exc))


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


def _register_routes(
    app: FastAPI,
    config: Settings,
    *,
    rate_handler: Callable[[Request], Awaitable[None]] | None = None,
) -> None:
    """Semua route HTTP — tanpa logika bisnis; error domain → HTTP status.

    Sprint 6 KR-1: `/health` terbuka (load-balancer); route API lain diberi
    deps keselamatan: API key (pilihan) + had kadar per-klien. `rate_handler`
    dibangun sekali di create_app (pemilik instance limiter = trim task).
    """

    router = APIRouter()
    auth_dep = Depends(_deps_guard(config))
    if rate_handler is None:
        rate_handler = _deps_rate_limit(config)[1]
    rate_dep = Depends(rate_handler)

    @router.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        model_ok = Path(config.model_path).exists()
        state = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        db_ok = False
        if state is not None:
            try:
                db_ok = bool(await state.ingest_jobs.ping())
            except Exception as exc:  # DB turun → degraded, bukan 500
                logger.warning("health_ping_failed", error=str(exc))
        db_state = "ok" if db_ok else "unreachable"
        return HealthResponse(
            status="ok" if model_ok and db_ok else "degraded",
            database=db_state,
            model_file="ok" if model_ok else "missing",
        )

    @router.post("/chat", response_model=None, dependencies=[auth_dep, rate_dep])
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

    @router.get(
        "/sessions/{session_id}/messages",
        response_model=SessionMessagesResponse,
        dependencies=[auth_dep, rate_dep],
    )
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

    @router.post("/ingest", response_model=IngestResponse, dependencies=[auth_dep, rate_dep])
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

    @router.get(
        "/ingest/jobs", response_model=IngestJobsResponse, dependencies=[auth_dep, rate_dep]
    )
    async def ingest_jobs(request: Request, limit: int = 50) -> IngestJobsResponse:
        state = getattr(request.app.state, STATE_REQUEST_ATTR, None)
        if state is None:
            raise HTTPException(status_code=503, detail="layanan belum siap")
        jobs = await state.ingest_jobs.list_jobs(limit=max(1, min(limit, 200)))
        return IngestJobsResponse(jobs=[_job_out(j) for j in jobs])

    @router.get("/ingest/jobs/{job_id}", dependencies=[auth_dep, rate_dep])
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
