"""Entitas domain chat & sitasi."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final


@dataclass(frozen=True, slots=True)
class Citation:
    """Sitasi wajib pada setiap jawaban (golden rule #8 dalam AGENTS.md)."""

    source: str
    chunk_index: int
    snippet: str


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """Chunk hasil retrieval dengan skor fusi RRF."""

    document_id: int
    source: str
    chunk_index: int
    content: str
    rrf_score: float
    metadata: dict[str, str | int | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievedContext:
    """Konteks retrieval untuk satu pertanyaan."""

    query: str
    chunks: tuple[RetrievedChunk, ...]

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    @property
    def citations(self) -> tuple[Citation, ...]:
        return tuple(
            Citation(source=c.source, chunk_index=c.chunk_index, snippet=c.content[:200])
            for c in self.chunks
        )


class RefusalReason(StrEnum):
    """Alasan penolakan jawaban pasti — diharapkan tak ada konteks relevan (golden rule #5)."""

    NO_RELEVANT_CONTEXT = "no_relevant_context"
    LOW_RELEVANCE_SCORE = "low_relevance_score"
    RETRIEVAL_UNAVAILABLE = "retrieval_unavailable"
    # Guard deterministik (pasca-Sprint 6): soalan keadaan peribadi — data
    # dinamik per-orang yang tiada dalam korpus dasar statik (mis. baki cuti
    # sendiri; kerana halusinasi borderline g011, docs/eval.md).
    PERSONAL_STATE_NOT_IN_CORPUS = "personal_state_not_in_corpus"


@dataclass(frozen=True, slots=True)
class ChatAnswer:
    """Hasil use case chat: jawaban grounded atau penolakan terstruktur."""

    content: str
    citations: tuple[Citation, ...] = ()
    refusal: RefusalReason | None = None
    is_refusal: bool = False

    @classmethod
    def grounded(cls, content: str, citations: tuple[Citation, ...]) -> ChatAnswer:
        return cls(content=content, citations=citations)

    @classmethod
    def refused(cls, reason: RefusalReason) -> ChatAnswer:
        return cls(
            content="Maaf, saya tidak menjumpai maklumat tersebut dalam dokumen yang tersedia.",
            refusal=reason,
            is_refusal=True,
        )


SESSION_ID_PATTERN: Final[str] = r"^[0-9a-fA-F-]{36}$"


@dataclass(frozen=True, slots=True)
class ChatCitationsEvent:
    """Event stream pertama: sitasi konteks yang memacu jawaban (golden rule #8)."""

    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class ChatTokenEvent:
    """Event stream: satu token delta dari LLM."""

    text: str


@dataclass(frozen=True, slots=True)
class ChatFinalEvent:
    """Event stream terakhir: jawaban lengkap (grounded atau penolakan)."""

    answer: ChatAnswer


ChatStreamEvent = ChatCitationsEvent | ChatTokenEvent | ChatFinalEvent


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Satu baris sejarah percakapan (KR-3, tabel chat_messages)."""

    role: str  # 'user' | 'assistant'
    content: str
    created_at: str | None = None
