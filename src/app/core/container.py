"""Container komposisi (composition root) — sambungkan port ke implementasi.

Clean architecture: DI manual, eksplisit, type-checkable mypy-strict. FastAPI
adapters menerima use case dari sini via Depends — tanpa framework DI.
Implementasi konkret riil (asyncpg/ONNX/llama.cpp) dibuat pada sprint 1-3;
stub testing dipasang di tests/.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.ports import Embedder, LLMGateway, VectorRepository
from app.application.use_cases.chat import ChatUseCase
from app.core.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class Container:
    """Semua use case yang siap pakai — dikomposisi sekali saat startup."""

    chat: ChatUseCase


def build_container(
    settings: Settings,
    *,
    embedder: Embedder,
    vector_repo: VectorRepository,
    llm: LLMGateway,
) -> Container:
    """Komposisi manual: infrastruktur disuntik, use case dirakit di dalam."""
    return Container(
        chat=ChatUseCase(
            embedder=embedder,
            vector_repo=vector_repo,
            llm=llm,
            top_k=settings.retriever_top_k,
            rrf_k=settings.rrf_k,
        )
    )


__all__ = ["Container", "build_container", "get_settings"]
