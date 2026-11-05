"""Use case chat: retrieval hybrid + RRF + generation grounded + penolakan.

Aturan (AGENTS.md golden rules #5, #8):
- tanpa konteks relevan → penolakan terstruktur, tanpa halusinasi;
- setiap jawaban membawa sitasi (source, chunk_index).
"""

from __future__ import annotations

import structlog

from app.application.ports import Embedder, LLMGateway, VectorRepository
from app.domain.chat import ChatAnswer, RefusalReason, RetrievedChunk, RetrievedContext
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
    ) -> None:
        self._embedder = embedder
        self._vector_repo = vector_repo
        self._llm = llm
        self._top_k = top_k
        self._rrf_k = rrf_k

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
        scores = dict(fused)
        chunks = tuple(
            RetrievedChunk(
                document_id=doc_id,
                source=source,
                chunk_index=chunk_index,
                content=content,
                rrf_score=scores[(doc_id, chunk_index)],
            )
            for doc_id, source, content, chunk_index in sorted(
                rows,
                key=lambda r: scores.get((r[0], r[3]), 0.0),
                reverse=True,
            )
        )
        context = RetrievedContext(query=question, chunks=chunks[: self._top_k])

        content = await self._llm.generate_grounded(question, context, history)
        log.bind(session_stage="chat", latency_stage="generation").info(
            "chat_done",
            n_chunks=len(context.chunks),
            history_turns=len(history),
        )
        return ChatAnswer.grounded(content=content, citations=context.citations)

    # ---- RRF (docs/retrieval.md): score = Σ 1/(RRF_K + rank), rank mulai 1 ----
    def _fuse_rrf(
        self,
        dense: list[tuple[int, int]],
        sparse: list[tuple[int, int]],
    ) -> list[tuple[tuple[int, int], float]]:
        scores: dict[tuple[int, int], float] = {}
        for lane in (dense, sparse):
            for rank, key in enumerate(lane, start=1):
                scores[key] = scores.get(key, 0.0) + 1.0 / (self._rrf_k + rank)
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
