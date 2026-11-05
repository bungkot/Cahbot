"""Container komposisi (composition root) — sambungkan port ke implementasi.

Clean architecture: DI manual, eksplisit, type-checkable mypy-strict. FastAPI
adapters menerima use case dari sini via Depends — tanpa framework DI.
`build_production_container` merakit implementasi riil (asyncpg/ONNX/llama.cpp);
tests menyuntik double (port stubs) — tidak pernah merakit infra.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

from app.application.ports import Embedder, IngestJobStore, LLMGateway, VectorRepository
from app.application.use_cases.chat import ChatUseCase
from app.application.use_cases.ingest import IngestUseCase
from app.core.config import Settings, get_settings
from app.infrastructure.onnx_embedder import OnnxEmbedder
from app.infrastructure.parsers.router import ParserRouter
from app.infrastructure.postgres.jobs import PgIngestJobRepository
from app.infrastructure.postgres.repository import PgVectorRepository
from app.infrastructure.text_chunker import TextChunker

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Container:
    """Semua use case + store siap pakai — dikomposisi sekali saat startup."""

    chat: ChatUseCase
    ingest: IngestUseCase
    ingest_jobs: IngestJobStore
    embedder: Embedder


def build_container(
    settings: Settings,
    *,
    embedder: Embedder,
    vector_repo: VectorRepository,
    llm: LLMGateway,
    job_store: IngestJobStore,
) -> Container:
    """Komposisi manual: infrastruktur disuntik, use case dirakit di dalam."""

    router = ParserRouter()
    chunker = TextChunker()
    return Container(
        chat=ChatUseCase(
            embedder=embedder,
            vector_repo=vector_repo,
            llm=llm,
            top_k=settings.retriever_top_k,
            rrf_k=settings.rrf_k,
        ),
        ingest=IngestUseCase(
            parser=router,
            chunker=chunker,
            embedder=embedder,
            vector_repo=vector_repo,
            embed_batch_size=settings.embed_batch_size,
        ),
        ingest_jobs=job_store,
        embedder=embedder,
    )


async def build_production_container(
    settings: Settings,
    *,
    llm: LLMGateway,
) -> Container:
    """Implementasi riil untuk runtime: pool Postgres + ONNX embedder + worker store.

    LLM masih dilempar sebagai port (dibangun pada Sprint 4 — llama.cpp gateway).
    Embedder dimuat MALAS oleh OnnxEmbedder; kegagalan model → log warning,
    aplikasi tetap hidup (endpoint ingest menerjemahkan error saat dipakai).
    """

    repo = await PgVectorRepository.create_from_env(
        settings.database_url, settings.db_pool_min, settings.db_pool_max
    )
    job_store = PgIngestJobRepository(repo.pool)
    embedder = OnnxEmbedder(
        settings.embed_model_path,
        tokenizer_path=settings.embed_tokenizer_path,
        max_seq_tokens=settings.embed_max_seq_tokens,
        threads=settings.embed_threads,
    )
    if not Path(settings.embed_model_path).exists():
        logger.warning("embed_model_missing", path=settings.embed_model_path)
    return build_container(
        settings,
        embedder=embedder,
        vector_repo=repo,
        llm=llm,
        job_store=job_store,
    )


__all__ = ["Container", "build_container", "build_production_container", "get_settings"]
