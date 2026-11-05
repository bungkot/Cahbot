"""Domain errors — di-raise di dalam, diterjemahkan di adapters ke HTTP."""

from __future__ import annotations


class DomainError(Exception):
    """Dasar seluruh error domain."""


class IngestError(DomainError):
    """Kegagalan pipeline ingest (parse/chunk/embed/upsert)."""


class RetrievalError(DomainError):
    """Kegagalan pencarian (retrieval)."""


class GenerationError(DomainError):
    """Kegagalan LLM menghasilkan jawaban."""
