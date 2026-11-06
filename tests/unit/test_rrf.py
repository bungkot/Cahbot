"""Unit: _fuse_rrf Hybrid Search — rumus score = Σ 1/(rrf_k + rank) (docs/retrieval.md)."""

from collections.abc import AsyncIterator

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
        return []

    async def embed_passage(self, text: str) -> list[float]:
        return []


class _StubRepository:
    # _fuse_rrf murni matematika — repo tak disentuh pada jalur ini
    async def search_dense(self, query_embedding: list[float], top_k: int) -> list[tuple[int, int]]:
        raise NotImplementedError

    async def search_dense_scored(
        self, query_embedding: list[float], top_k: int
    ) -> list[tuple[int, int, float]]:
        raise NotImplementedError

    async def search_sparse(self, query: str, top_k: int) -> list[tuple[int, int]]:
        raise NotImplementedError

    async def fetch_chunks(self, keys: list[tuple[int, int]]) -> list[tuple[int, str, str, int]]:
        raise NotImplementedError

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
        return "stub"

    async def warm_up(self) -> None:
        return None

    async def generate_grounded(
        self,
        question: str,
        context: RetrievedContext,
        history: tuple[tuple[str, str], ...],
    ) -> str:
        raise NotImplementedError

    def stream_grounded(
        self,
        question: str,
        context: RetrievedContext,
        history: tuple[tuple[str, str], ...],
    ) -> AsyncIterator[str]:
        raise NotImplementedError


def _use_case() -> ChatUseCase:
    return ChatUseCase(
        _StubEmbedder(),
        _StubRepository(),
        _StubLLM(),
    )


def test_fuse_kedua_lane_kosong_menghasilkan_kosong() -> None:
    uc = _use_case()
    assert uc._fuse_rrf([], []) == []


def test_fuse_skor_kunci_dua_lane_dijumlahkan() -> None:
    """Kunci yang muncul di dense & sparse memperoleh Σ 1/(k+rank)."""
    uc = _use_case()
    fused = dict(uc._fuse_rrf([(1, 2), (2, 0)], [(1, 2), (3, 1)]))
    k = float(uc._rrf_k)
    # (1,2) rank1 di kedua lane
    assert fused[(1, 2)] == pytest.approx(2.0 / (k + 1))
    # rank2 dense saja
    assert fused[(2, 0)] == pytest.approx(1.0 / (k + 2))
    # rank2 sparse saja
    assert fused[(3, 1)] == pytest.approx(1.0 / (k + 2))


def test_fuse_urutan_skor_menuruni_atas_ke_bawah() -> None:
    """Kunci di dua lane harus teratas; kunci dari satu lane mengikuti rank."""
    uc = _use_case()
    keys = [temp_key[0] for temp_key in uc._fuse_rrf([(5, 0)], [(5, 0), (6, 0), (7, 0)])]
    assert keys == [(5, 0), (6, 0), (7, 0)]
