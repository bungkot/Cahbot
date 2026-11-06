"""Request/response schemas (Pydantic) — lapisan adapters, bukan domain."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.chat import SESSION_ID_PATTERN, Citation, RefusalReason

_MAX_HISTORY_TURNS = 20
_MAX_HISTORY_MESSAGE_LEN = 2000


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # UUID v4 sahaja — tanpa ini persist sejarah senyap gagal ($1::uuid, review MAJOR/MINOR).
    session_id: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)
    # Sprint 4: false → ChatResponse (kompatibel); true → SSE aliran token.
    stream: bool = False
    # Sejarah tanpa had = inflasi prompt (DoS) — diikat 20 giliran.
    history: tuple[tuple[str, str], ...] = Field(default=(), max_length=_MAX_HISTORY_TURNS)

    @field_validator("history")
    @classmethod
    def _bound_history(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        """Pastikan item sejarah midat-kan saiz (tanpa 2000 aksara per item)."""

        for q, a in value:
            if len(q) > _MAX_HISTORY_MESSAGE_LEN or len(a) > _MAX_HISTORY_MESSAGE_LEN:
                raise ValueError("mesej sejarah melebihi 2000 aksara")
        return value


class CitationOut(BaseModel):
    source: str
    chunk_index: int
    snippet: str


class ChatResponse(BaseModel):
    content: str
    citations: list[CitationOut] = []
    refusal: RefusalReason | None = None
    is_refusal: bool = False


class ChatTokenFrame(BaseModel):
    """SSE frame `token`: satu delta teks dari LLM."""

    text: str


class ChatCitationsFrame(BaseModel):
    """SSE frame `citations`: konteks sitasi yang menopang jawaban."""

    citations: list[CitationOut]


class ChatFinalFrame(BaseModel):
    """SSE frame `final`: jawaban lengkap (grounded atau penolakan)."""

    content: str
    citations: list[CitationOut] = []
    refusal: RefusalReason | None = None
    is_refusal: bool = False


class SessionMessageOut(BaseModel):
    """Satu mesej sejarah sesi (GET /sessions/{id}/messages)."""

    role: str
    content: str
    created_at: str | None = None


class SessionMessagesResponse(BaseModel):
    """Sejarah sesi mengikut masa menaik (KR-3)."""

    session_id: str
    messages: list[SessionMessageOut]


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
    "ChatCitationsFrame",
    "ChatFinalFrame",
    "ChatRequest",
    "ChatResponse",
    "ChatTokenFrame",
    "Citation",
    "CitationOut",
    "HealthResponse",
    "IngestJobOut",
    "IngestJobsResponse",
    "IngestResponse",
    "RefusalReason",
    "SessionMessageOut",
    "SessionMessagesResponse",
]
