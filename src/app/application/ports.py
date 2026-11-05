"""Ports — kontrak (Protocol) yang diimplementasi infrastructure.

Perjanjian Clean Architecture: use case HANYA melihat ports ini, tidak pernah
modul infrastructure langsung. Semua metode async; blok sinkron berat ditangani
di implementation via asyncio.to_thread (docs/conventions/code.md).
"""

from __future__ import annotations

from typing import Protocol

from app.domain.chat import RetrievedContext
from app.domain.documents import Chunk, ParsedDocument


class Embedder(Protocol):
    """Embedding lokal ONNX (e5-small, 384d) — implementasi: infrastructure/onnx_embedder."""

    async def embed_query(self, text: str) -> list[float]:
        """Embed pertanyaan (prefix `query:` — syarat model e5)."""
        ...

    async def embed_passage(self, text: str) -> list[float]:
        """Embed potongan dokumen (prefix `passage:`)."""
        ...


class VectorRepository(Protocol):
    """Penyimpanan chunks/documents di Postgres+pgvector (implementasi: infrastructure/postgres)."""

    async def search_dense(self, query_embedding: list[float], top_k: int) -> list[tuple[int, int]]:
        """Kembalikan [(document_id, chunk_index)] urut kemiripan cosine (HNSW)."""
        ...

    async def search_sparse(self, query: str, top_k: int) -> list[tuple[int, int]]:
        """Kembalikan [(document_id, chunk_index)] urut skor FTS (GIN tsvector)."""
        ...

    async def fetch_chunks(self, keys: list[tuple[int, int]]) -> list[tuple[int, str, str, int]]:
        """Ambil (document_id, source, content, chunk_index) untuk kunci retrieval."""
        ...

    async def upsert_document(
        self,
        source: str,
        content_hash: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> None:
        """Idempoten: documents upsert pada source; ganti chunk bila hash berubah."""
        ...


class DocumentParser(Protocol):
    """Parser file → ParsedDocument (PyMuPDF4LLM / MarkItDown; dipilih per ekstensi)."""

    def parse(self, data: bytes, source: str) -> ParsedDocument: ...


class Chunker(Protocol):
    """Chunking ~512 token, overlap ~64 — tidak membelah heading/tabel (skill: rag-dev)."""

    def chunk(self, document: ParsedDocument) -> list[Chunk]: ...


class LLMGateway(Protocol):
    """Gateway llama.cpp lokal — menghasilkan jawaban grounded Bahasa Melayu."""

    async def generate_grounded(
        self,
        question: str,
        context: RetrievedContext,
        history: tuple[tuple[str, str], ...],
    ) -> str:
        """Jawab HANYA dari konteks; bila memadai → tolak (assertion di use case)."""
        ...
