"""Ports — kontrak (Protocol) yang diimplementasi infrastructure.

Perjanjian Clean Architecture: use case HANYA melihat ports ini, tidak pernah
modul infrastructure langsung. Semua metode async; blok sinkron berat ditangani
di implementation via asyncio.to_thread (docs/conventions/code.md).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.domain.chat import ChatMessage, RetrievedContext
from app.domain.documents import Chunk, IngestJob, ParsedDocument


class Embedder(Protocol):
    """Embedding lokal ONNX (e5-small, 384d) — implementasi: infrastructure/onnx_embedder."""

    @property
    def model_name(self) -> str:
        """Nama model disimpan ke metadata chunk (konvensi data-model.md)."""
        ...

    async def warm_up(self) -> None:
        """Muat model + cek dimensi sekali di lifespan (komposisi startup)."""
        ...

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

    async def search_dense_scored(
        self, query_embedding: list[float], top_k: int
    ) -> list[tuple[int, int, float]]:
        """Sama seperti search_dense + cosine similarity `[-1..1]` (KR-4).

        Skor dipakai dasar penolakan — RRF hanya pantulan rank, sedangkan
        cosine memisahkas soalan jauh-dari-korpus dari terdekat.
        """
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
        metadata: list[dict[str, str | int | None]] | None = None,
    ) -> None:
        """Idempoten: hash sama → no-op; berbeda → hapus chunk lama + tulis baru.

        1 transaksi penuh — SEMUA chunk dikirim sekali panggil (bukan per batch).
        """
        ...


class IngestJobStore(Protocol):
    """Antrean ingest di tabel `ingest_jobs` (implementasi: infrastructure/postgres/jobs)."""

    async def enqueue(self, path: str) -> int:
        """Tambah job `pending` untuk path (POSIX, relatif ke folder inbox)."""
        ...

    async def claim_pending(self) -> IngestJob | None:
        """Ambil 1 job `pending` teratas → `running` (FOR UPDATE SKIP LOCKED)."""
        ...

    async def mark_done(self, job_id: int) -> None:
        """Tandai job selesai."""
        ...

    async def mark_failed_or_retry(self, job_id: int, error: str, *, max_attempts: int) -> bool:
        """attempts+1; requeue `pending` bila attempts < max_attempts, lain `failed`.

        Kembalikan True bila job di-requeue, False bila berstatus gagal final.
        """
        ...

    async def get_job(self, job_id: int) -> IngestJob | None: ...

    async def list_jobs(self, limit: int = 50) -> list[IngestJob]: ...


class DocumentParser(Protocol):
    """Parser file → ParsedDocument (PyMuPDF4LLM / MarkItDown; dipilih per ekstensi)."""

    def parse(self, data: bytes, source: str) -> ParsedDocument: ...


class Chunker(Protocol):
    """Chunking ~512 token, overlap ~64 — tidak membelah heading/tabel (skill: rag-dev)."""

    def chunk(self, document: ParsedDocument) -> list[Chunk]: ...


class LLMGateway(Protocol):
    """Gateway llama.cpp lokal — menghasilkan jawaban grounded Bahasa Melayu."""

    @property
    def model_name(self) -> str:
        """Nama model untuk log KR-5 (`model=...` per permintaan)."""
        ...

    async def warm_up(self) -> None:
        """Muat model SEKALI di lifespan (KR-1) — placeholder punya no-op."""

    async def generate_grounded(
        self,
        question: str,
        context: RetrievedContext,
        history: tuple[tuple[str, str], ...],
    ) -> str:
        """Jawab HANYA dari konteks; bila memadai → tolak (assertion di use case)."""
        ...

    def stream_grounded(
        self,
        question: str,
        context: RetrievedContext,
        history: tuple[tuple[str, str], ...],
    ) -> AsyncIterator[str]:
        """Stream token delta jawaban grounded (Sprint 4). Generator async."""
        ...


class ChatHistoryRepository(Protocol):
    """Sejarah percakapan di Postgres (KR-3; implementasi: postgres.chat).

    Semua query di lapisan ini — parametrised $1..$n (golden rule #4).
    """

    async def ensure_session(self, session_id: str) -> None:
        """Pastikan session wujud; INSERT ON CONFLICT DO NOTHING."""
        ...

    async def append_message(self, session_id: str, role: str, content: str) -> None:
        """Tambah satu mesej ('user' | 'assistant') ke chat_messages."""
        ...

    async def recent_messages(self, session_id: str, limit: int) -> list[ChatMessage]:
        """N mesej terakhir sesi mengikut masa menaik (masa kosong bila sesi tidak ada)."""
        ...
