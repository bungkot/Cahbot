"""Entiti domain dokumen & ingest."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DocumentStatus(StrEnum):
    INDEXED = "indexed"


@dataclass(frozen=True, slots=True)
class Document:
    """Satu dokumen terindeks — identitas unik pada `source` (schema SQL)."""

    id: int
    source: str
    content_hash: str
    status: DocumentStatus
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """Satu unit teks hasil parser (muka surat PDF / keseluruhan file markdown).

    `page` ialah nombor halaman asal (PDF) atau None bila format tidak berhalaman.
    Heading TIDAK disimpan di sini — dikenal pasti oleh Chunker dari baris `#`.
    """

    text: str
    page: int | None = None

    def __post_init__(self) -> None:
        if self.page is not None and self.page < 1:
            msg = "page mesti >= 1 (1-based)"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Hasil parsing sebelum chunking — senarai blok teks (markdown-ish)."""

    source: str
    content_hash: str
    blocks: tuple[ParsedBlock, ...] = ()

    def __post_init__(self) -> None:
        if not self.source:
            msg = "source tidak boleh kosong"
            raise ValueError(msg)

    @property
    def text(self) -> str:
        """Teks penuh (gabungan blok) — untuk debug/eval, bukan untuk chunking."""
        return "\n\n".join(b.text for b in self.blocks)


@dataclass(frozen=True, slots=True)
class Chunk:
    """Potongan teks siap embed. `(source, chunk_index)` adalah kunci sitasi."""

    chunk_index: int
    content: str
    heading: str | None = None
    page: int | None = None
    n_tokens: int = 0  # anggaran token (untuk metadata chunk_size_tokens)

    def __post_init__(self) -> None:
        if not self.content.strip():
            msg = "chunk content tidak boleh kosong"
            raise ValueError(msg)
        if self.chunk_index < 0:
            msg = "chunk_index harus >= 0"
            raise ValueError(msg)
        if self.n_tokens < 0:
            msg = "n_tokens tidak boleh negatif"
            raise ValueError(msg)


def content_hash(data: bytes) -> str:
    """SHA-256 hex — kunci idempotensi ingest (documents.content_hash)."""
    return hashlib.sha256(data).hexdigest()


class IngestJobStatus(StrEnum):
    """Kitaran hayat baris ingest_jobs (schema SQL)."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IngestJob:
    """Satu baris antrean ingest (ingest_jobs) — dibaca worker watcher/poller."""

    id: int
    path: str
    status: IngestJobStatus
    attempts: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Ringkasan hasil pelaksanaan ingest satu dokumen."""

    source: str
    content_hash: str
    n_chunks: int
    is_new_or_replaced: bool = True


def chunk_metadata(
    chunks: list[Chunk],
    *,
    embed_model: str,
    lang: str,
) -> list[dict[str, str | int | None]]:
    """Metadata JSONB per chunk (konvensi docs/data-model.md § Chunk metadata).

    Kunci sitasi (source, chunk_index) tidak dimasukkan di sini — sudah ada
    di kolom tabel chunks.
    """

    return [
        {
            "embed_model": embed_model,
            "lang": lang,
            "heading": chunk.heading,
            "page": chunk.page,
            "chunk_size_tokens": chunk.n_tokens if chunk.n_tokens else None,
        }
        for chunk in chunks
    ]


# Anggaran token deterministik & murah: 1 kiraan ≈ 1 ruang-terpisah kata
# (cukup untuk pemiawaian chunker ~512 token tanpa tokenizer berat).
def estimate_tokens(text: str) -> int:
    """Anggaran token word-level — mesti deterministik & murah (tanpa tokenizer)."""

    return len(text.split())
