"""Unit: KR-3 execute() wajib menghabiskan aliran — persist sejarah berlaku.

Bug asal: `return` awal pada loop event tidak menghabiskan generator — persist
dan log chat_done tidak pernah berjalan. Ujian ini mencengah regersi itu.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.application.use_cases.chat import ChatUseCase
from app.domain.chat import ChatMessage
from app.domain.documents import Chunk

pytestmark = pytest.mark.unit


class _StubEmbedder:
    @property
    def model_name(self) -> str:
        return "stub"

    async def warm_up(self) -> None:
        return None

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * 384

    async def embed_passage(self, text: str) -> list[float]:
        return [0.0] * 384


class _StubRepository:
    def __init__(self, dense: list[tuple[int, int, float]], chunks: dict[int, str]) -> None:
        self._dense = dense
        self._chunks = chunks

    async def search_dense(self, query_embedding: list[float], top_k: int) -> list[tuple[int, int]]:
        return [(d, c) for d, c, _cos in self._dense[:top_k]]

    async def search_dense_scored(
        self, query_embedding: list[float], top_k: int
    ) -> list[tuple[int, int, float]]:
        return self._dense[:top_k]

    async def search_sparse(self, query: str, top_k: int) -> list[tuple[int, int]]:
        return []

    async def fetch_chunks(self, keys: list[tuple[int, int]]) -> list[tuple[int, str, str, int]]:
        return [(d, f"d{d}.pdf", self._chunks[d], c) for d, c in keys]

    async def upsert_document(
        self,
        source: str,
        content_hash: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        metadata: list[dict[str, str | int | None]] | None = None,
    ) -> None:
        raise NotImplementedError


class _StubLLM:
    @property
    def model_name(self) -> str:
        return "stub-llm"

    def __init__(self) -> None:
        self.seen_history: tuple[tuple[str, str], ...] | None = None

    async def warm_up(self) -> None:
        return None

    async def generate_grounded(
        self,
        question: str,
        context: object,
        history: tuple[tuple[str, str], ...],
    ) -> str:
        self.seen_history = history
        return "jawapan"

    def stream_grounded(
        self,
        question: str,
        context: object,
        history: tuple[tuple[str, str], ...],
    ) -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            self.seen_history = history
            yield "jawapan"

        return _gen()


class _StubHistoryStore:
    """Double `ChatHistoryRepository` — merekam pemanggilan + sejarah sintetik."""

    def __init__(self) -> None:
        self.sessions: list[str] = []
        self.saved: list[tuple[str, str, str]] = []  # (session_id, role, content)

    async def ensure_session(self, session_id: str) -> None:
        if session_id not in self.sessions:
            self.sessions.append(session_id)

    async def append_message(self, session_id: str, role: str, content: str) -> None:
        self.saved.append((session_id, role, content))

    async def recent_messages(self, session_id: str, limit: int) -> list[ChatMessage]:
        return (
            [
                ChatMessage(role="user", content="soal lama"),
                ChatMessage(role="assistant", content="jawab lama"),
            ]
            if session_id in self.sessions
            else []
        )


async def test_execute_melakukan_persist_sejarah() -> None:
    """execute() menghabiskan aliran: ensure_session + append user & assistant."""

    store = _StubHistoryStore()
    use_case = ChatUseCase(
        _StubEmbedder(),
        _StubRepository([(1, 0, 0.90)], {1: "a"}),
        _StubLLM(),
        top_k=4,
        history_store=store,
    )

    await use_case.execute("q", "s1")

    assert store.sessions == ["s1"]
    assert (store.saved[0][1], store.saved[0][2]) == ("user", "q")
    assert store.saved[1][1] == "assistant"


async def test_history_dimuat_bila_sesi_sudah_ada() -> None:
    """Giliran ke-2 dengan SID sama: sejarah dari store kerja sampai gateway.

    Store double mengembalikan pasangan sintetik ('soal lama','jawab lama')
    untuk sesi terdaftar — pasangan itu mESTI tiba sebagai arg history LLM.
    """

    store = _StubHistoryStore()
    llm = _StubLLM()
    use_case = ChatUseCase(
        _StubEmbedder(),
        _StubRepository([(1, 0, 0.90)], {1: "a"}),
        llm,
        top_k=4,
        history_store=store,
    )

    await use_case.execute("q", "s1")  # giliran pertama — daftar sesi + simpan
    await use_case.execute("q2", "s1")  # giliran kedua — muat sejarah ke prompt

    assert llm.seen_history == (("soal lama", "jawab lama"),)
    assert len(store.saved) == 4  # dua giliran x (user, assistant)


async def test_tanpa_session_tak_persist() -> None:
    """session_id None + store wajib → tiada kena mengena dengan sejarah."""

    store = _StubHistoryStore()
    use_case = ChatUseCase(
        _StubEmbedder(),
        _StubRepository([(1, 0, 0.90)], {1: "a"}),
        _StubLLM(),
        top_k=4,
        history_store=store,
    )

    await use_case.execute("q")

    assert store.sessions == []
    assert store.saved == []
