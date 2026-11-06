"""Use case chat: retrieval hybrid + RRF + generation grounded + penolakan.

Aturan (AGENTS.md golden rules #5, #8):
- tanpa konteks relevan → penolakan terstruktur, tanpa halusinasi;
- setiap jawaban membawa sitasi (source, chunk_index).
"""

from __future__ import annotations

import structlog

from app.application.context import build_context, fuse_rrf
from app.application.ports import Embedder, LLMGateway, VectorRepository
from app.domain.chat import ChatAnswer, RefusalReason, RetrievedChunk
from app.domain.errors import RetrievalError

logger = structlog.get_logger(__name__)


class ChatUseCase:
    """Orkestrasi alur tanya-jawab. Semua dependensi = ports."""

    def __init__(
        self,
        embedder: Embedder,
        vector_repo: VectorRepository,
        llm: LLMGateway,
        *,
        top_k: int = 4,
        rrf_k: int = 60,
        max_context_tokens: int = 2500,
    ) -> None:
        self._embedder = embedder
        self._vector_repo = vector_repo
        self._llm = llm
        self._top_k = top_k
        self._rrf_k = rrf_k
        self._max_context_tokens = max_context_tokens

    async def execute(
        self,
        question: str,
        history: tuple[tuple[str, str], ...] = (),
    ) -> ChatAnswer:
        log = logger.bind(session_stage="chat", model_stage="retrieval")
        embedding = await self._embedder.embed_query(question)

        # Hybrid: dense HNSW + sparse GIN (jalur paralel secara konseptual; pool asyncpg menangani)
        try:
            dense = await self._vector_repo.search_dense(embedding, self._top_k)
            sparse = await self._vector_repo.search_sparse(question, self._top_k)
        except Exception as exc:  # pragma: no cover — dipetakan adapters
            log.error("retrieval_failed", error=str(exc))
            msg = "pengambilan konteks gagal"
            raise RetrievalError(msg) from exc

        fused = self._fuse_rrf(dense, sparse)
        if not fused:
            log.info("no_relevant_context", top_k=self._top_k)
            return ChatAnswer.refused(RefusalReason.NO_RELEVANT_CONTEXT)

        rows = await self._vector_repo.fetch_chunks([key for key, _ in fused])
        chunks = self._order_chunks(rows, dict(fused))
        context = build_context(
            question,
            chunks,
            max_tokens=self._max_context_tokens,
            max_chunks=self._top_k,
        )

        content = await self._llm.generate_grounded(question, context, history)
        log.bind(session_stage="chat", latency_stage="generation").info(
            "chat_done",
            n_chunks=len(context.chunks),
            history_turns=len(history),
        )
        return ChatAnswer.grounded(content=content, citations=context.citations)

    # ---- urutkan chunk mengikut skor RRF (terbaik dulu) ---------------------
    def _order_chunks(
        self,
        rows: list[tuple[int, str, str, int]],
        scores: dict[tuple[int, int], float],
    ) -> tuple[RetrievedChunk, ...]:
        """Rows dari repo → RetrievedChunk diurut skor RRF menurun (stabil)."""

        ordered = sorted(rows, key=lambda r: scores.get((r[0], r[3]), 0.0), reverse=True)
        return tuple(
            RetrievedChunk(
                document_id=doc_id,
                source=source,
                chunk_index=chunk_index,
                content=content,
                rrf_score=scores[(doc_id, chunk_index)],
            )
            for doc_id, source, content, chunk_index in ordered
        )

    # ---- RRF (docs/retrieval.md): score = Σ 1/(RRF_K + rank), rank mulai 1 ----
    def _fuse_rrf(
        self,
        dense: list[tuple[int, int]],
        sparse: list[tuple[int, int]],
    ) -> list[tuple[tuple[int, int], float]]:
        return fuse_rrf(dense, sparse, self._rrf_k)
