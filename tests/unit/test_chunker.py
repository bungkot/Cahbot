"""Unit: chunker boundary — heading/tabel tidak dipecah, overlap, index, token."""

from __future__ import annotations

import pytest

from app.domain.documents import ParsedDocument, estimate_tokens
from app.infrastructure.text_chunker import TextChunker


def _doc(*texts: str) -> ParsedDocument:
    from app.domain.documents import ParsedBlock

    return ParsedDocument(
        source="u.pdf", content_hash="h", blocks=tuple(ParsedBlock(text=t) for t in texts)
    )


def test_dokumen_kosong_tidak_menghasilkan_chunk() -> None:
    chunker = TextChunker()
    assert chunker.chunk(_doc()) == []


def test_dokumen_pendek_satu_chunk_index0() -> None:
    chunker = TextChunker()
    chunks = chunker.chunk(_doc("satu dua tiga empat lima"))
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].content == "satu dua tiga empat lima"


def test_index_chunk_berurutan() -> None:
    chunker = TextChunker(target_tokens=8, overlap_tokens=2)
    paragraf = "\n\n".join(f"pola {i} " + "kata " * 10 for i in range(3))
    chunks = chunker.chunk(_doc(paragraf))
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_heading_terbawa_ke_chunk_dan_muncul_di_konten() -> None:
    chunker = TextChunker(target_tokens=12, overlap_tokens=3)
    text = "# Dasar Cuti\n" + "\n".join("kata" for _ in range(20))
    chunks = chunker.chunk(_doc(text))
    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.heading == "Dasar Cuti"
        assert chunk.content.startswith("# Dasar Cuti")


def test_tabel_tidak_dipecah_ditengah_baris() -> None:
    chunker = TextChunker(target_tokens=20, overlap_tokens=4)
    rows = "\n".join(f"| kolom {i} | data-{i} nilai panjang besar besar besar |" for i in range(8))
    chunks = chunker.chunk(_doc(rows))
    assert len(chunks) >= 2  # benar-benar meluap
    original_lines = set(rows.splitlines())
    for chunk in chunks:
        for line in chunk.content.splitlines():
            assert line in original_lines  # tiap baris utuh, tiada baris baru ciptaan


def test_overlap_menyalin_ekor_unit_sebelumnya() -> None:
    chunker = TextChunker(target_tokens=16, overlap_tokens=6)
    kps: list[str] = [f"pola {i} " + "kata " * 10 for i in range(6)]
    chunks = chunker.chunk(_doc("\n\n".join(kps)))
    assert len(chunks) >= 2
    assert chunks[1].content != chunks[0].content
    # ekor chunk pertama muncul kembali di chunk kedua (overlap utuh/kaftan kata)
    tail_words = chunks[0].content.split()[-6:]
    assert " ".join(tail_words) in chunks[1].content.replace("\n", " ")


def test_paragraf_besar_pipecah_di_batas_kalimat() -> None:
    chunker = TextChunker(target_tokens=30, overlap_tokens=5)
    sentences: list[str] = []
    for i in range(8):
        words = " ".join(f"k{i}{j}" for j in range(10))  # 10 kata per kalimat
        sentences.append(f"Kalimat {i} mengandung {words}.")
    text = " ".join(sentences)
    chunks = chunker.chunk(_doc(text))
    assert len(chunks) >= 2
    for chunk in chunks:
        assert estimate_tokens(chunk.content) <= 30 + 10  # toleransi 1 kalimat penuh
        # kalimat tidak dipecah: ada kalimat utuh di setiap chunk yang mengandung kalimat
    kalimat_utuh = "Kalimat 3 mengandung k30 k31 k32 k33 k34 k35 k36 k37 k38 k39."
    assert any(kalimat_utuh in c.content for c in chunks)


def test_halaman_mengikuti_blok_asal() -> None:
    from app.domain.documents import ParsedBlock

    doc = ParsedDocument(
        source="u.pdf",
        content_hash="h",
        blocks=(
            ParsedBlock(text="# Bahagian A\nisi halaman tiga panjang panjang", page=3),
            ParsedBlock(text="# Bahagian B\nisi halaman empat panjang panjang", page=4),
        ),
    )
    chunker = TextChunker()
    chunks = chunker.chunk(doc)
    assert {c.page for c in chunks} == {3, 4}


def test_overlap_midflush_mempertahankan_heading() -> None:
    """Luap target di tengah seksyen — chunk lanjutan tetap membawa heading aktif."""

    chunker = TextChunker(target_tokens=12, overlap_tokens=3)
    text = "# Kesimpulan\n" + "\n".join("kata" for _ in range(40))
    chunks = chunker.chunk(_doc(text))
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.heading == "Kesimpulan"


def test_anggaran_token_deterministik() -> None:
    text = "satu dua tiga\n\nempat"
    assert estimate_tokens(text) == len(text.split())


def test_config_chunker_tidak_valid() -> None:
    with pytest.raises(ValueError, match="overlap"):
        TextChunker(target_tokens=10, overlap_tokens=10)
