"""Use case chat: retrieval hybrid + RRF + generation grounded + penolakan.

Aturan (AGENTS.md 1. kualitas #5, #8, #10):
- konteks tak relevan → penolakan terstruktur, tanpa halusinasi;
- setiap jawaban membawa sitasi (source, chunk_index);
- log per-permintaan membawa session_id / model / latency_ms (KR-5).

KR-3: sejarah dibaca & disimpan via ChatHistoryRepository (bila diwire);
KR-4: penolakan bila skor tertinggi fusi RRF di bawah ambang.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import structlog

from app.application.context import build_context, fuse_rrf
from app.application.ports import (
    ChatHistoryRepository,
    Embedder,
    LLMGateway,
    VectorRepository,
)
from app.domain.chat import (
    ChatAnswer,
    ChatCitationsEvent,
    ChatFinalEvent,
    ChatStreamEvent,
    ChatTokenEvent,
    RefusalReason,
    RetrievedChunk,
)
from app.domain.errors import RetrievalError

logger = structlog.get_logger(__name__)

HistoryTurn = tuple[str, str]


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
        refusal_threshold: float = 0.0,
        history_store: ChatHistoryRepository | None = None,
        history_turns: int = 6,
    ) -> None:
        """`history_turns` dihitung MESEJ (bukan pasangan) — 6 mesej = 3 giliran."""
        self._embedder = embedder
        self._vector_repo = vector_repo
        self._llm = llm
        self._top_k = top_k
        self._rrf_k = rrf_k
        self._max_context_tokens = max_context_tokens
        # KR-4: cosine top-1 dense < ambang → penolakan (ambang <= -1.0 = fitur
        # mati; default diukur dari data golden — docs/eval.md).
        self._refusal_threshold = refusal_threshold
        self._history_store = history_store
        self._history_turns = history_turns

    # ---- execute (non-stream, kompatibel adapter lama) ----------------------

    async def execute(
        self,
        question: str,
        session_id: str | None = None,
        history_override: tuple[HistoryTurn, ...] = (),
    ) -> ChatAnswer:
        """Jalankan alur penuh; kembalikan jawaban akhir (grounded / penolakan).

        Aliran DIBACA HINGGA HABIS (bukan return awal) — langkah pas-final
        (persistensi sejarah + log chat_done) wajib berjalan.
        """

        final_event: ChatFinalEvent | None = None
        async for event in self.execute_stream(
            question, session_id, history_override=history_override
        ):
            if isinstance(event, ChatFinalEvent):
                final_event = event
        if final_event is None:
            msg = "aliran selesai tanpa event final"
            raise RetrievalError(msg)
        return final_event.answer

    # ---- execute_stream (DoD: /chat streaming) -------------------------------

    async def execute_stream(
        self,
        question: str,
        session_id: str | None = None,
        history_override: tuple[HistoryTurn, ...] = (),
    ) -> AsyncIterator[ChatStreamEvent]:
        """Aliran event: citations → token delta → final. Simpan sejarah di akhir."""

        log = logger.bind(session_id=session_id, stage="chat")
        started = time.perf_counter()
        history = await self._load_history(session_id, history_override)
        model_name = self._llm.model_name

        embedding = await self._embedder.embed_query(question)
        try:
            dense_scored = await self._vector_repo.search_dense_scored(embedding, self._top_k)
            sparse = await self._vector_repo.search_sparse(question, self._top_k)
        except Exception as exc:
            log.error("retrieval_failed", error=str(exc))
            msg = "pengambilan konteks gagal"
            raise RetrievalError(msg) from exc

        dense = [(doc_id, chunk_idx) for doc_id, chunk_idx, _cos in dense_scored]
        # KR-4: cosine top-1 (dense) — isyarat penolakan terukur (docs/eval.md).
        # None: lane dense kosong (FTS sahaja) → bukan asas penolakan cosine.
        top1_cosine: float | None = dense_scored[0][2] if dense_scored else None
        log.info(
            "retrieval_fused", top1_cosine=None if top1_cosine is None else round(top1_cosine, 4)
        )

        fused = self._fuse_rrf(dense, sparse)
        if not fused:
            log.info("no_relevant_context", top_k=self._top_k)
            refusal = ChatAnswer.refused(RefusalReason.NO_RELEVANT_CONTEXT)
            yield ChatFinalEvent(answer=refusal)
            await self._persist(session_id, question, refusal)
            self._log_chat_done(log, started, model_name, refused=True)
            return

        # KR-4: cosine terbaik (dengan bukti dense) di bawah ambang → penolakan.
        if top1_cosine is not None and top1_cosine < self._refusal_threshold:
            log.info("refused_low_relevance", top1_cosine=round(top1_cosine, 4))
            refusal = ChatAnswer.refused(RefusalReason.LOW_RELEVANCE_SCORE)
            yield ChatFinalEvent(answer=refusal)
            await self._persist(session_id, question, refusal)
            self._log_chat_done(log, started, model_name, refused=True)
            return

        rows = await self._vector_repo.fetch_chunks([key for key, _ in fused])
        chunks = self._order_chunks(rows, dict(fused))
        context = build_context(
            question,
            chunks,
            max_tokens=self._max_context_tokens,
            max_chunks=self._top_k,
        )
        # Golden #8: sitasi keluar lebih dulu — sebelum delta token pertama.
        yield ChatCitationsEvent(citations=context.citations)

        content_parts: list[str] = []
        async for token in self._llm.stream_grounded(question, context, history):
            content_parts.append(token)
            yield ChatTokenEvent(text=token)

        answer = ChatAnswer.grounded(content="".join(content_parts), citations=context.citations)
        yield ChatFinalEvent(answer=answer)
        await self._persist(session_id, question, answer)
        self._log_chat_done(
            log,
            started,
            model_name,
            refused=False,
            n_chunks=len(context.chunks),
            history_turns=len(history),
        )

    # ---- sejarah (KR-3) ------------------------------------------------------

    async def _load_history(
        self,
        session_id: str | None,
        override: tuple[HistoryTurn, ...],
    ) -> tuple[HistoryTurn, ...]:
        """Sejarah: override klien menang; selain itu muat N giliran terakhir.

        Fail-soft: ralat DB baca sejarah → warning + sejarah kosong — chat
        grounded TIDAK wajib mati kerana sejarah (rule #5).
        """

        if override:
            return override
        if session_id is None or self._history_store is None:
            return ()
        try:
            messages = await self._history_store.recent_messages(session_id, self._history_turns)
        except Exception as exc:
            logger.warning("history_load_failed", session_id=session_id, error=str(exc))
            return ()
        # Pasangan (soalan, jawaban): user aktif lebih dahulu, lalu assistant.
        turns: list[HistoryTurn] = []
        pending_user: str | None = None
        for msg in messages:
            if msg.role == "user":
                pending_user = msg.content
            elif msg.role == "assistant" and pending_user is not None:
                turns.append((pending_user, msg.content))
                pending_user = None
        return tuple(turns)

    async def _persist(
        self,
        session_id: str | None,
        question: str,
        answer: ChatAnswer,
    ) -> None:
        """Simpan soalan user + jawaban assistant. Gagal → log, chat tidak gagal."""

        if session_id is None or self._history_store is None:
            return
        try:
            await self._history_store.ensure_session(session_id)
            await self._history_store.append_message(session_id, "user", question)
            await self._history_store.append_message(session_id, "assistant", answer.content)
        except Exception as exc:
            logger.warning("history_persist_failed", session_id=session_id, error=str(exc))

    # ---- helper --------------------------------------------------------------

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
                rrf_score=scores.get((doc_id, chunk_index), 0.0),
            )
            for doc_id, source, content, chunk_index in ordered
        )

    def _log_chat_done(
        self,
        log: structlog.stdlib.BoundLogger,
        started: float,
        model_name: str,
        *,
        refused: bool,
        n_chunks: int = 0,
        history_turns: int = 0,
    ) -> None:
        """KR-5: satu event log wajib per permintaan chat."""

        log.info(
            "chat_done",
            model=model_name,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            n_chunks=n_chunks,
            history_turns=history_turns,
            refused=refused,
        )

    # ---- RRF (docs/retrieval.md): score = Σ 1/(RRF_K + rank), rank mulai 1 ----
    def _fuse_rrf(
        self,
        dense: list[tuple[int, int]],
        sparse: list[tuple[int, int]],
    ) -> list[tuple[tuple[int, int], float]]:
        return fuse_rrf(dense, sparse, self._rrf_k)
