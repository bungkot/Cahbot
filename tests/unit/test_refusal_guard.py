"""Unit: guard penafian keadaan-peribadi (pasca-Sprint 6) — tingkat & use case.

Bekas kes g011 ("Berapa baki cuti tahunan saya yang tinggal?") menghalusinasi
pada LLM 1.7B (docs/eval.md § Sprint 4 verification). Guard deterministik ini
menolak SEBELUM embed/LLM; ujian memastikan (a) kondisi classifer tepat atas
kes golden, (b) jalur use case menolak tanpa menyentuh embedder/LLM.
"""

from collections.abc import AsyncIterator

import pytest

from app.application.refusal_guard import is_personal_state_query
from app.application.use_cases.chat import ChatUseCase
from app.domain.chat import ChatFinalEvent, ChatTokenEvent, RefusalReason
from app.domain.documents import Chunk

pytestmark = pytest.mark.unit

# ---- fungsi murni -----------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # kes golden digenap — g011 & varian
        ("Berapa baki cuti tahunan saya yang tinggal?", True),  # g011
        ("Berapa hari cuti saya yang tinggal?", True),  # g022
        ("Berapa baki klaim perjalanan saya yang belum dibayar?", True),  # g021
        ("Berapa baki gaji saya pada bulan ini?", True),  # g023
        ("Aku nak tahu baki cuti berapa hari tinggal", True),  # casefold + kata "aku"
        ("cuti saya berbaki berapa hari?", True),
        # kes dasar peribadi — TETAP boleh-jawab → tidak menolak
        ("Bolehkah baki cuti saya dibawa ke tahun hadapan?", False),  # g024
        ("Adakah baki cuti saya boleh diwarisi?", False),
        # tanpa kata-ganti diri pertama → tidak menolak
        ("Bolehkah baki cuti tahunan dibawa ke tahun hadapan?", False),  # g014
        ("Berapa lama cuti bersalin yang layak diterima?", False),  # g005
        # tanpa kata baki/tinggal → tidak menolak (kes dasar dgn "saya")
        ("Bila klaim perjalanan saya akan dibayar?", False),  # g010
        ("Apa jadi kalau saya tidak serahkan sijil doktor?", False),  # g004
        ("Berapa hari cuti tahunan saya?", False),  # entitlement — dari korpus
        # homonim "tinggal" (menduduki) tanpa soalan kuantiti → tidak menolak
        ("Saya tinggal di Shah Alam, ada elaun perumahan?", False),
        # keluarga soalan proses/dasar ("macam mana"/"bagaimana") → tidak menolak
        ("Macam mana nak cek baki cuti saya?", False),
        # luwes: baris kosong / nonsoalan
        ("", False),
        ("   ", False),
    ],
)
def test_is_personal_state_query(question: str, expected: bool) -> None:
    assert is_personal_state_query(question) is expected


# ---- use case: guard mendahului embed/LLM -----------------------------------


class _StubEmbedder:
    def __init__(self) -> None:
        self.called = False

    @property
    def model_name(self) -> str:
        return "stub"

    async def warm_up(self) -> None:
        return None

    async def embed_query(self, text: str) -> list[float]:
        self.called = True
        return [0.0] * 384

    async def embed_passage(self, text: str) -> list[float]:
        self.called = True
        return [0.0] * 384


class _StubRepository:
    """Tidak boleh disentuh pada jalur penolakan guard."""

    def __init__(self) -> None:
        self.called = False

    async def search_dense(self, query_embedding: list[float], top_k: int) -> list[tuple[int, int]]:
        self.called = True
        return [(1, 0)]

    async def search_dense_scored(
        self, query_embedding: list[float], top_k: int
    ) -> list[tuple[int, int, float]]:
        self.called = True
        return [(1, 0, 0.90)]

    async def search_sparse(self, query: str, top_k: int) -> list[tuple[int, int]]:
        self.called = True
        return []

    async def fetch_chunks(self, keys: list[tuple[int, int]]) -> list[tuple[int, str, str, int]]:
        self.called = True
        return [(k[0], "stub.pdf", "kandungan", k[1]) for k in keys]

    async def upsert_document(
        self,
        source: str,
        content_hash: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        metadata: list[dict[str, str | int | None]] | None = None,
    ) -> None:
        raise NotImplementedError


class _StubLLM:
    def __init__(self) -> None:
        self.called = False

    @property
    def model_name(self) -> str:
        return "stub-llm"

    async def warm_up(self) -> None:
        return None

    async def generate_grounded(
        self,
        question: str,
        context: object,
        history: tuple[tuple[str, str], ...],
    ) -> str:
        self.called = True
        return "jawapan grounded"

    def stream_grounded(
        self,
        question: str,
        context: object,
        history: tuple[tuple[str, str], ...],
    ) -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            self.called = True
            yield "jawapan"

        return _gen()


def _use_case() -> tuple[ChatUseCase, _StubEmbedder, _StubRepository, _StubLLM]:
    embedder = _StubEmbedder()
    repo = _StubRepository()
    llm = _StubLLM()
    uc = ChatUseCase(embedder, repo, llm, top_k=4, refusal_threshold=0.82)
    return uc, embedder, repo, llm


async def test_guard_menolak_tanpa_retrieval_llm() -> None:
    """g011 → penolakan PERSONAL_STATE tanpa embed/repo/LLM (penentu, murah)."""

    uc, embedder, repo, llm = _use_case()
    events = [e async for e in uc.execute_stream("Berapa baki cuti tahunan saya yang tinggal?")]
    assert len(events) == 1  # hanyalah final — tanpa sitasi/delta token
    final = next(e for e in events if isinstance(e, ChatFinalEvent))
    assert final.answer.is_refusal is True
    assert final.answer.refusal == RefusalReason.PERSONAL_STATE_NOT_IN_CORPUS
    assert not final.answer.citations  # penolakan tanpa sitasi (konvensi UI)
    assert embedder.called is False
    assert repo.called is False
    assert llm.called is False
    assert not any(isinstance(e, ChatTokenEvent) for e in events)


async def test_guard_tidak_menolak_soalan_dasar_peribadi() -> None:
    """g024 ("Bolehkah baki cuti saya dibawa...") → jalur grounded biasa."""

    uc, embedder, repo, llm = _use_case()
    events = [
        e async for e in uc.execute_stream("Bolehkah baki cuti saya dibawa ke tahun hadapan?")
    ]
    final = next(e for e in events if isinstance(e, ChatFinalEvent))
    assert final.answer.is_refusal is False
    assert embedder.called is True
    assert repo.called is True
    assert llm.called is True  # LLM dipanggil — bukan penolakan guard
