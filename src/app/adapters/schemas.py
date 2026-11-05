"""Request/response schemas (Pydantic) — lapisan adapters, bukan domain."""

from __future__ import annotations

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


class Citations(BaseModel):
    """Kontrak sitasi: wajib berasal dari domain, tidak pernah direkayasa manual."""

    citations: list[Citation]


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "Citation",
    "CitationOut",
    "HealthResponse",
    "RefusalReason",
]
