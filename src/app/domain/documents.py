"""Entitas domain dokumen & ingest."""

from __future__ import annotations

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
class ParsedDocument:
    """Hasil parsing mentah sebelum chunking (teks markdown-ish + metadata)."""

    source: str
    content_hash: str
    text: str


@dataclass(frozen=True, slots=True)
class Chunk:
    """Potongan teks siap embed. `(source, chunk_index)` adalah kunci sitasi."""

    chunk_index: int
    content: str
    heading: str | None = None
    page: int | None = None

    def __post_init__(self) -> None:
        if not self.content.strip():
            msg = "chunk content tidak boleh kosong"
            raise ValueError(msg)
        if self.chunk_index < 0:
            msg = "chunk_index harus >= 0"
            raise ValueError(msg)
