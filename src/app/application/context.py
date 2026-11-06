"""Context builder — fusi RRF + had token konteks (Sprint 3, docs/retrieval.md).

Murni logika aplikasi (tanpa import teknologi) agar dipakai bersama oleh
`ChatUseCase` dan runner eval. Fungsi fusi dipindahkan dari `ChatUseCase`
supaya satu-satunya implementasi (rumus RRF ditulis sekali saja).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.chat import RetrievedChunk, RetrievedContext

# Anggaran token kasar Bahasa Melayu untuk tokenizer e5/Qwen: ~4 aksara ≈ 1 token.
# Anggaran konservatif untuk trim — TIDAK menggantikan tokenizer asli (Sprint 4).
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Anggar token kandungan (konservatif: sisa aksara dibundarkan ke atas)."""

    if not text:
        return 0
    return -(-len(text) // CHARS_PER_TOKEN)  # ceil


def fuse_rrf(
    dense: Sequence[tuple[int, int]],
    sparse: Sequence[tuple[int, int]],
    rrf_k: int = 60,
) -> list[tuple[tuple[int, int], float]]:
    """RRF (docs/retrieval.md): score = Σ 1/(rrf_k + rank), rank mulai 1."""

    scores: dict[tuple[int, int], float] = {}
    for lane in (dense, sparse):
        for rank, key in enumerate(lane, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def build_context(
    query: str,
    chunks: Sequence[RetrievedChunk],
    *,
    max_tokens: int,
    max_chunks: int | None = None,
) -> RetrievedContext:
    """Susun konteks dalam had token tanpa memotong chunk terbaik.

    Strategi (policy conflicts, docs/retrieval.md):
    - Chunk pertama (RRF tertinggi) SELALU masuk — kualiti chunk terbaik
      tidak digadai demi had.
    - Chunk berikutnya ditambah berurutan skor selagi anggaran token tidak
      melebihi `max_tokens` dan bilangan chunk tidak melebihi `max_chunks`
      (kontrak RETRIEVER_TOP_K: bilangan chunk yang masuk prompt).
    - **Overflow pertama menghentikan urutan** (bukan skip-lalu-teruskan):
      chunk lebih kecil selepasnya pun gugur — konteks kekal berurutan-skor
      tanpa celah.

    Input dianggap sudah diurut skor RRF menurun (unsur pertama = terbaik).
    """

    if not chunks:
        return RetrievedContext(query=query, chunks=())

    kept: list[RetrievedChunk] = [chunks[0]]  # terbaik — tak pernah dipotong
    budget = max_tokens - estimate_tokens(kept[0].content)
    for chunk in chunks[1:]:
        if max_chunks is not None and len(kept) >= max_chunks:
            break
        budget -= estimate_tokens(chunk.content)
        if budget < 0:
            break  # penuh — berhenti; kekal urutan skor
        kept.append(chunk)

    return RetrievedContext(query=query, chunks=tuple(kept))
