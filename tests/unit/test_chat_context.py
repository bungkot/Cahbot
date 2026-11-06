"""Unit: ChatUseCase menyusun konteks ikut budget + top-k (Sprint 3 KR-2)."""

import pytest

from app.application.use_cases.chat import ChatUseCase
from app.domain.chat import RetrievedContext
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
    """Repo doubel: jalur dense di-suntik; jalur sparse sentiasa kosong."""

    def __init__(self, dense: list[tuple[int, int]], chunks: dict[int, str]) -> None:
        self._dense = dense
        self._chunks = chunks

    async def search_dense(self, query_embedding: list[float], top_k: int) -> list[tuple[int, int]]:
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
    def __init__(self) -> None:
        self.seen_context: RetrievedContext | None = None

    async def generate_grounded(
        self,
        question: str,
        context: RetrievedContext,
        history: tuple[tuple[str, str], ...],
    ) -> str:
        self.seen_context = context
        return "jawapan"


def _make_use_case(
    dense: list[tuple[int, int]],
    chunks: dict[int, str],
    **kwargs: int,
) -> tuple[ChatUseCase, _StubLLM]:
    """Susun use case + simpan stub LLM eksplisit (tak sentuh attr peribadi)."""

    embedder = _StubEmbedder()
    repo = _StubRepository(dense, chunks)
    llm = _StubLLM()
    uc = ChatUseCase(embedder, repo, llm, **kwargs)
    return uc, llm


async def test_chat_menghormati_topk() -> None:
    """6 hasil dense → hanya 4 (top_k) yang masuk prompt."""

    dense = [(i, 0) for i in range(1, 7)]
    chunks = {i: "x" * 100 for i in range(1, 7)}
    uc, llm = _make_use_case(dense, chunks, top_k=4, max_context_tokens=5000)

    answer = await uc.execute("pertanyaan")
    assert llm.seen_context is not None
    assert len(llm.seen_context.chunks) == 4
    assert answer.citations  # sitasi wajib (golden rule #8)


async def test_chat_kekal_chunk_terbaik_bila_budget_penuh() -> None:
    """Budget hanya cukup untuk 2 chunk — chunk skor tertinggi SENTIASA kekal."""

    dense = [(i, 0) for i in range(1, 4)]
    chunks = {i: "x" * 1600 for i in range(1, 4)}  # ~400 token setiap satu
    uc, llm = _make_use_case(dense, chunks, top_k=4, max_context_tokens=800)

    await uc.execute("pertanyaan")
    assert llm.seen_context is not None
    assert len(llm.seen_context.chunks) == 2
    assert llm.seen_context.chunks[0].document_id == 1  # terbaik kekal
