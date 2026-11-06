"""Prompt grounded Bahasa Melayu (Sprint 4 KR-2) — tanya-jawab HANYA dari konteks.

Aturan (golden rule #5, #8 + skill rag-dev):
- jawaban HANYA dari konteks; konteks tidak memadai → pesan penolakan baku;
- setiap fakta WAJIB disitasi sugai `[sumber: <fail>, bahagian ke-<N>]` — N
  adalah chunk_index yang ditambah satu (basis-1, ara gaya sitasi UI);
- bukan hafalan markup prompt dijanak dari input user — semua kandungan
  dimasukkan sebagai bahagian mesej, bukan sisipan system.
"""

from __future__ import annotations

from app.domain.chat import RetrievedContext

ROLE_SYSTEM: str = "system"
ROLE_USER: str = "user"
ROLE_ASSISTANT: str = "assistant"

# Mesej penolakan baku (golden rule #5) — dikembalikan verbatim saat
# konteks tidak memadai (instruksi konsisten dgn domain.chat.ChatAnswer.refused).
REFUSAL_INSTRUCTION: str = (
    "bila konteks tidak mengandungi jawabannya, jawab TEPAT dengan:"
    ' "Maaf, saya tidak menjumpai maklumat tersebut dalam dokumen yang tersedia."'
)

SYSTEM_PROMPT: str = (
    "Anda ialah Cahbot, pembantu tanya-jawab dokumen dalam Bahasa Melayu.\n"
    "ATURAN WAJIB:\n"
    "1. Jawab HANYA berdasarkan konteks yang diberikan. " + REFUSAL_INSTRUCTION + "\n"
    "2. Setiap fakta dari konteks WAJIB disitasi format"
    " [sumber: <nama fail>, bahagian ke-<N>].\n"
    "3. Jangan menambah maklumat dari luar konteks, jangan menerka, jangan membuang fakta.\n"
    "4. Jawab ringkas dan padat dalam Bahasa Melayu."
)


def _context_block(context: RetrievedContext) -> str:
    """Konteks → teks berlabel. Sitasi menyertai sumber & bahagian 1-based."""

    parts: list[str] = []
    for chunk in context.chunks:
        bahagian = chunk.chunk_index + 1
        parts.append(f"[sumber: {chunk.source}, bahagian ke-{bahagian}]\n{chunk.content}")
    return "\n\n".join(parts)


def build_grounded_messages(
    question: str,
    context: RetrievedContext,
    history: tuple[tuple[str, str], ...] = (),
) -> list[dict[str, str]]:
    """Susun mesej format-chat untuk LLM: system → sejarah → soalan+konteks.

    `history` berupa (soalan_lama, jawaban_lama) — tertua dahulu; pengehadan
    bilangan giliran dilakukan oleh use case (bukan di sini).
    """

    messages: list[dict[str, str]] = [{"role": ROLE_SYSTEM, "content": SYSTEM_PROMPT}]
    for past_q, past_a in history:
        messages.append({"role": ROLE_USER, "content": past_q})
        messages.append({"role": ROLE_ASSISTANT, "content": past_a})

    user_content = f"KONTEKS:\n{_context_block(context)}\n\nSOALAN: {question}"
    messages.append({"role": ROLE_USER, "content": user_content})
    return messages
