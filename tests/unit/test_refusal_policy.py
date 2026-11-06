"""Unit: dasar penolakan (Sprint 4 KR-4) — ambang cosine top-1 dense di use case."""

from collections.abc import AsyncIterator

import pytest

from app.application.use_cases.chat import ChatUseCase
from app.domain.chat import ChatCitationsEvent, ChatFinalEvent, ChatTokenEvent, RefusalReason
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
    """Repo double: baris dense(skor cosine) di-suntik; sparse di-suntik juga."""

    def __init__(
        self,
        dense_scored: list[tuple[int, int, float]],
        chunks: dict[int, str],
        sparse: list[tuple[int, int]] | None = None,
    ) -> None:
        self._dense = dense_scored
        self._chunks = chunks
        self._sparse = sparse or []

    async def search_dense(self, query_embedding: list[float], top_k: int) -> list[tuple[int, int]]:
        return [(d, c) for d, c, _cos in self._dense[:top_k]]

    async def search_dense_scored(
        self, query_embedding: list[float], top_k: int
    ) -> list[tuple[int, int, float]]:
        return self._dense[:top_k]

    async def search_sparse(self, query: str, top_k: int) -> list[tuple[int, int]]:
        return self._sparse[:top_k]

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
    """Ketika ditolak ia TIDAK boleh dipanggil — dilacak dengan bendera `called`."""

    def __init__(self) -> None:
        self.called = False

    @property
    def model_name(self) -> str:
        return "stub-llm"

    async def warm_up(self) -> None:
        return None

    async def generate_grounded(
        self,
        question: str,
        context: object,
        history: tuple[tuple[str, str], ...],
    ) -> str:
        self.called = True
        return "jawapan grounded"

    def stream_grounded(
        self,
        question: str,
        context: object,
        history: tuple[tuple[str, str], ...],
    ) -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            self.called = True
            yield "jawapan "
            yield "grounded"

        return _gen()


_DENSE: list[tuple[int, int, float]] = [(1, 0, 0.90), (2, 0, 0.88)]
_CHUNKS: dict[int, str] = {1: "a", 2: "b", 3: "c"}


def _use_case(
    threshold: float,
    *,
    dense: list[tuple[int, int, float]] | None = None,
    sparse: list[tuple[int, int]] | None = None,
) -> tuple[ChatUseCase, _StubLLM]:
    llm = _StubLLM()
    uc = ChatUseCase(
        _StubEmbedder(),
        _StubRepository(dense if dense is not None else _DENSE, _CHUNKS, sparse=sparse),
        llm,
        top_k=4,
        refusal_threshold=threshold,
    )
    return uc, llm


async def test_cosine_di_atas_ambang_tak_menolak() -> None:
    """cosine top-1 0.90 > ambang 0.82 → jalankan grounded dengan sitasi."""

    uc, llm = _use_case(threshold=0.82)
    events = [e async for e in uc.execute_stream("q")]
    assert llm.called is True  # jalur grounded boleh memanggil LLM
    assert any(isinstance(e, ChatCitationsEvent) for e in events)
    assert any(isinstance(e, ChatTokenEvent) for e in events)
    final = next(e for e in events if isinstance(e, ChatFinalEvent))
    assert final.answer.is_refusal is False
    assert final.answer.content == "jawapan grounded"


async def test_ambang_tinggi_menolak_tanpa_panggil_llm() -> None:
    """Ambang 0.95 > cosine 0.90 → penolakan LOW_RELEVANCE_SCORE, LLM tak dipanggil."""

    uc, llm = _use_case(threshold=0.95)
    events = [e async for e in uc.execute_stream("q")]
    final = next(e for e in events if isinstance(e, ChatFinalEvent))
    assert final.answer.is_refusal is True
    assert final.answer.refusal == RefusalReason.LOW_RELEVANCE_SCORE
    assert not final.answer.citations  # penolakan tanpa sitasi (konvensi UI)
    assert llm.called is False  # penolakan TIDAK memanggil LLM — tidak terhalusinasi
    assert not any(isinstance(e, ChatTokenEvent) for e in events)
    assert not any(isinstance(e, ChatCitationsEvent) for e in events)


async def test_ambang_negratif_fitur_mati() -> None:
    """threshold ≤ -1.0 → cosine tidak pernah di bawahnya → penolakan mati."""

    uc, _ = _use_case(threshold=-1.0)
    events = [e async for e in uc.execute_stream("q")]
    final = next(e for e in events if isinstance(e, ChatFinalEvent))
    assert final.answer.is_refusal is False


async def test_dense_kosong_sparse_hanya_tidak_menolak() -> None:
    """Lane dense kosong (sentinel -1.0) + FTS ada kandungan → jalur grounded.

    Match FTS tetap bukti konteks; penolakan tidak boleh menimpa jalur dense
    kosong secara senyap.
    """

    uc, llm = _use_case(threshold=0.82, dense=[], sparse=[(3, 0)])
    events = [e async for e in uc.execute_stream("q")]
    final = next(e for e in events if isinstance(e, ChatFinalEvent))
    assert final.answer.is_refusal is False
    assert llm.called is True
