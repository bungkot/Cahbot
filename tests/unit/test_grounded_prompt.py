"""Unit: prompt grounded BM (Sprint 4 KR-2) — hanya konteks + sitasi + tolak."""

import pytest

from app.application.grounded_prompt import (
    REFUSAL_INSTRUCTION,
    ROLE_SYSTEM,
    SYSTEM_PROMPT,
    build_grounded_messages,
)
from app.domain.chat import RetrievedChunk, RetrievedContext

pytestmark = pytest.mark.unit


def _context() -> RetrievedContext:
    chunk = RetrievedChunk(
        document_id=5,
        source="handbook-cuti.pdf",
        chunk_index=2,
        content="Cuti tahunan 12 hari.",
        rrf_score=0.03,
    )
    return RetrievedContext(query="berapa cuti tahunan?", chunks=(chunk,))


def test_prompt_mesti_mengandungi_konteks() -> None:
    messages = build_grounded_messages("berapa cuti tahunan?", _context())
    system = messages[0]
    user = messages[-1]

    assert system["role"] == ROLE_SYSTEM
    assert "12 hari" in user["content"]
    assert "berapa cuti tahunan?" in user["content"]
    assert REFUSAL_INSTRUCTION in system["content"]


def test_prompt_sitasi_bebas_index_1() -> None:
    """'bahagian ke-3' untuk chunk_index 2 (basis-1, seperti sitasi UI)."""

    content = build_grounded_messages("q", _context())[-1]["content"]
    assert "[sumber: handbook-cuti.pdf, bahagian ke-3]" in content


def test_prompt_system_mengandungi_aturan_dan_penolakan() -> None:
    assert "HANYA" in SYSTEM_PROMPT
    assert "Maaf, saya tidak menjumpai maklumat tersebut" in SYSTEM_PROMPT


def test_prompt_sejarah_terletak_antara_system_dan_user() -> None:
    history = (("s1", "a1"), ("s2", "a2"))
    messages = build_grounded_messages("q", _context(), history)

    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
    assert messages[1]["content"] == "s1" and messages[2]["content"] == "a1"


def test_prompt_tanpa_history() -> None:
    messages = build_grounded_messages("q", _context(), ())
    assert len(messages) == 2  # system + user
