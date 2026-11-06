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

from app.application.ports import (
    ChatHistoryRepository,
    Embedder,
    IngestJobStore,
    LLMGateway,
    VectorRepository,
)
from app.application.use_cases.chat import ChatUseCase
from app.application.use_cases.ingest import IngestUseCase
from app.core.config import Settings, get_settings
from app.infrastructure.llama_cpp_gateway import LlamaCppGateway
from app.infrastructure.onnx_embedder import OnnxEmbedder
from app.infrastructure.parsers.router import ParserRouter
from app.infrastructure.placeholder_llm import PlaceholderLLMGateway
from app.infrastructure.postgres.chat import PgChatHistoryRepository
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
    llm: LLMGateway
    history: ChatHistoryRepository


def build_container(
    settings: Settings,
    *,
    embedder: Embedder,
    vector_repo: VectorRepository,
    llm: LLMGateway,
    job_store: IngestJobStore,
    history_store: ChatHistoryRepository,
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
            max_context_tokens=settings.context_max_tokens,
            refusal_threshold=settings.retrieval_refusal_threshold,
            history_store=history_store,
            history_turns=settings.chat_history_turns,
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
        llm=llm,
        history=history_store,
    )


async def build_production_container(settings: Settings) -> Container:
    """Implementasi riil untuk runtime: pool Postgres + ONNX + gateway LLM riil.

    KR-1: bila fail GGUF wujud → LlamaCppGateway (dimuat malas di lifespan);
    tiada → PlaceholderLLMGateway + warning (aplikasi tetap hidup, jawaban
    placeholder menyatakan jujur — golden rule #5).
    """

    repo = await PgVectorRepository.create_from_env(
        settings.database_url,
        settings.db_pool_min,
        settings.db_pool_max,
        hnsw_ef_search=settings.hnsw_ef_search,
    )
    job_store = PgIngestJobRepository(repo.pool)
    history_store = PgChatHistoryRepository(repo.pool)
    embedder = OnnxEmbedder(
        settings.embed_model_path,
        tokenizer_path=settings.embed_tokenizer_path,
        max_seq_tokens=settings.embed_max_seq_tokens,
        threads=settings.embed_threads,
    )
    if not Path(settings.embed_model_path).exists():
        logger.warning("embed_model_missing", path=settings.embed_model_path)

    llm: LLMGateway
    if Path(settings.model_path).exists():
        llm = LlamaCppGateway(
            settings.model_path,
            n_ctx=settings.llm_n_ctx,
            n_batch=settings.llm_n_batch,
            threads=settings.llm_threads,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
    else:
        logger.warning("llm_model_missing_using_placeholder", path=settings.model_path)
        llm = PlaceholderLLMGateway()

    return build_container(
        settings,
        embedder=embedder,
        vector_repo=repo,
        llm=llm,
        job_store=job_store,
        history_store=history_store,
    )


__all__ = ["Container", "build_container", "build_production_container", "get_settings"]
