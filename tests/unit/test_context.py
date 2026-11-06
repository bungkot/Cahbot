"""Unit: context builder (Sprint 3 KR-2) — had token, terbaik tak pernah dipotong."""

import pytest

from app.application.context import build_context, estimate_tokens, fuse_rrf
from app.domain.chat import RetrievedChunk

pytestmark = pytest.mark.unit


def _chunk(doc: int, idx: int, content: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=doc,
        source=f"d{doc}.pdf",
        chunk_index=idx,
        content=content,
        rrf_score=score,
    )


# --- estimate_tokens -------------------------------------------------------


def test_estimate_tokens_kosong_berniali_nol() -> None:
    assert estimate_tokens("") == 0


def test_estimate_tokens_bundarkan_ke_atas() -> None:
    # 9 aksara → ceil(9/4) = 3
    assert estimate_tokens("abcdefghi") == 3


# --- fuse_rrf (direct — satu kelas implementasi yang sama dipakai eval) ------


def test_fuse_rrf_fusi_dua_lane() -> None:
    fused = dict(fuse_rrf([(1, 2)], [(1, 2), (3, 1)], rrf_k=60))
    assert fused[(1, 2)] == pytest.approx(2.0 / 61)
    assert fused[(3, 1)] == pytest.approx(1.0 / 62)


# --- build_context ---------------------------------------------------------


def test_build_context_kosong() -> None:
    context = build_context("q", [], max_tokens=1000)
    assert context.is_empty


def test_build_context_semua_muat_dalam_budget() -> None:
    chunks = [_chunk(1, i, "x" * 80, s) for i, s in enumerate((0.9, 0.8, 0.7), start=1)]
    context = build_context("q", chunks, max_tokens=1000)
    assert len(context.chunks) == 3
    # urutan skor menurun dikekalkan
    assert [c.rrf_score for c in context.chunks] == [0.9, 0.8, 0.7]


def test_build_context_terbaik_menetap_walaupun_lebih_budget() -> None:
    """Chunk terbaik TIDAK pernah dipotong walaupun melangkau seluruh budget."""

    big = _chunk(1, 0, "x" * 4000, 1.0)  # ~1000 token > budget 500
    context = build_context("q", [big], max_tokens=500)
    assert len(context.chunks) == 1
    assert context.chunks[0] is big


def test_build_context_bisik_weakest_mengikut_budget() -> None:
    """Budget hanya untuk 1-2 chunk — yang lemah digugurkan, bukan dipotong."""

    c1 = _chunk(1, 0, "x" * 400, 1.0)  # 100 tok
    c2 = _chunk(2, 1, "y" * 400, 0.9)  # 100 tok
    c3 = _chunk(3, 2, "z" * 400, 0.8)  # 100 tok → melebihi budget 280 tok
    context = build_context("q", [c1, c2, c3], max_tokens=280)
    assert len(context.chunks) == 2
    assert context.chunks[0].rrf_score == 1.0
    assert context.chunks[1].rrf_score == 0.9


def test_build_context_max_chunks_cap() -> None:
    """Kontrak top_k: bilangan chunk juga dibatasi walau token cukup."""

    chunks = [_chunk(i, 0, "x" * 40, 1.0 - i * 0.1) for i in range(1, 7)]
    context = build_context("q", chunks, max_tokens=10_000, max_chunks=4)
    assert len(context.chunks) == 4
