"""Unit: schemas transport (Pydantic) — validasi HTTP di lapisan adapters."""

import pytest
from pydantic import ValidationError

from app.adapters.schemas import ChatRequest, ChatResponse, CitationOut
from app.domain.chat import Citation, RefusalReason

pytestmark = pytest.mark.unit


def test_question_tidak_boleh_kosong() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(question="")


def test_question_batas_panjang_2000() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(question="x" * 2001)
    assert ChatRequest(question="x" * 2000).question == "x" * 2000


def test_history_default_kosong() -> None:
    assert ChatRequest(question="berapa sisa cuti?").history == ()


def test_chat_response_sitasi_dari_domain() -> None:
    """Sitasi hanya boleh direkayasa dari domain Citation (kontrak adapters)."""
    cite = Citation(source="handbook.pdf", chunk_index=3, snippet="cuti 12 hari")
    out = ChatResponse(
        content="jawaban",
        citations=[
            CitationOut(source=cite.source, chunk_index=cite.chunk_index, snippet=cite.snippet)
        ],
        refusal=None,
        is_refusal=False,
    )
    assert out.citations[0].chunk_index == 3


def test_chat_response_refusal_tanpa_sitasi() -> None:
    out = ChatResponse(
        content="tidak ditemukan",
        citations=[],
        refusal=RefusalReason.NO_RELEVANT_CONTEXT,
        is_refusal=True,
    )
    assert out.is_refusal is True
    assert out.citations == []
