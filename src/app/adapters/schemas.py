"""Request/response schemas (Pydantic) — lapisan adapters, bukan domain."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.chat import Citation, RefusalReason


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    history: tuple[tuple[str, str], ...] = ()


class CitationOut(BaseModel):
    source: str
    chunk_index: int
    snippet: str


class ChatResponse(BaseModel):
    content: str
    citations: list[CitationOut] = []
    refusal: RefusalReason | None = None
    is_refusal: bool = False


class HealthResponse(BaseModel):
    status: str
    database: str
    model_file: str


class IngestResponse(BaseModel):
    """Hasil POST /ingest serentak (MVP — pipeline berjalan hingga selesai)."""

    source: str
    status: Literal["done", "failed"]
    n_chunks: int
    content_hash: str


class IngestJobOut(BaseModel):
    """Satu baris antrean ingest untuk status polling."""

    id: int
    path: str
    status: str
    attempts: int
    error: str | None = None


class IngestJobsResponse(BaseModel):
    jobs: list[IngestJobOut]


class Citations(BaseModel):
    """Kontrak sitasi: wajib berasal dari domain, tidak pernah direkayasa manual."""

    citations: list[Citation]


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "Citation",
    "CitationOut",
    "HealthResponse",
    "IngestJobOut",
    "IngestJobsResponse",
    "IngestResponse",
    "RefusalReason",
]
