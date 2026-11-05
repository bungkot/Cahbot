"""Placeholder LLMGateway — penambung lintas sprint (Sprint 2 → 4).

Sprint 4 membangun `infrastructure/llama_cpp_gateway.py` sesungguhnya. Sebelum
itu, ChatUseCase tetap butuh port LLMGateway: golden rule #5 melarang halusinasi,
sehingga placeholder TIDAK mengarang jawaban — ia menyatakan jujur bahwa
generasi LLM belum didayakan dan menyarankan kutipan konteks (yang sudah
grounded dari retrieval).
"""

from __future__ import annotations

from typing import Final

from app.domain.chat import RetrievedContext

PLACEHOLDER_MESSAGE: Final[str] = (
    "Generasi jawaban (LLM) belum didayakan pada MVP ini. "
    "Sila lihat rujukan konteks di bawah — ia diambil dari dokumen tersedia."
)


class PlaceholderLLMGateway:
    """Implementasi `LLMGateway` sementara — deterministik, tanpa model."""

    async def generate_grounded(
        self,
        question: str,
        context: RetrievedContext,
        history: tuple[tuple[str, str], ...],
    ) -> str:
        return PLACEHOLDER_MESSAGE
