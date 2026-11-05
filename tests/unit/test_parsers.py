"""Unit: parser infrastruktur — PDF (pymupdf), MarkItDown (md), fallback, dispatch."""

from __future__ import annotations

from typing import Any, cast

import pymupdf
import pytest
from markitdown import MarkItDown

from app.domain.errors import IngestError
from app.infrastructure.parsers.fallback_parser import PlainTextParser
from app.infrastructure.parsers.markitdown_parser import MarkItDownParser
from app.infrastructure.parsers.pdf_parser import PyMuPdfParser
from app.infrastructure.parsers.router import ParserRouter


def _pdf_bytes(n_pages: int = 2) -> bytes:
    """PDF kecil buatan (offline) — halaman dengan teks biasa."""
    doc = pymupdf.open()  # type: ignore[no-untyped-call]  # stub pymupdf parsial
    for i in range(n_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Halaman {i + 1}")
        page.insert_text((72, 100), "Kandungan penting perkhidmatan awam.")
        page.insert_text((72, 128), "Perkhidmatan bermula jam 8.00 pagi.")
    return doc.tobytes(garbage=3, deflate=True)  # type: ignore[no-untyped-call,no-any-return]


def test_pdf_parser_dua_halaman_dengan_metadata_page() -> None:
    parsed = PyMuPdfParser().parse(_pdf_bytes(2), "laporan.pdf")
    assert len(parsed.blocks) == 2
    assert parsed.blocks[0].page is not None and parsed.blocks[0].page >= 1
    pages = [int(b.page or 0) for b in parsed.blocks]
    assert pages[1] > pages[0]  # hubungan bertambah, apa pun konvensi 0/1-based
    assert "Kandungan penting" in parsed.text


def test_pdf_gagal_dibuka_lempar_ingesteror() -> None:
    with pytest.raises(IngestError):
        PyMuPdfParser().parse(b"bukan pdf", "pecah.pdf")


def test_markitdown_md_ke_satu_block() -> None:
    md = b"# Panduan\n\n- langkah 1\n- langkah 2\n"
    parsed = MarkItDownParser().parse(md, "panduan.md")
    assert len(parsed.blocks) == 1
    assert "Panduan" in parsed.blocks[0].text
    assert parsed.blocks[0].page is None


def test_fallback_teks_utf8_dan_buang_karakter_kontrol() -> None:
    bytes_in = b"Permulaan\x00\x08 teks biasa.\x7f"
    parsed = PlainTextParser().parse(bytes_in, "catatan.xyz")
    assert parsed.blocks[0].text == "Permulaan teks biasa."
    assert parsed.blocks[0].text == "Permulaan teks biasa."


def test_fallback_biner_tolak_ingesteror() -> None:
    with pytest.raises(IngestError):
        PlainTextParser().parse(b"\x00\x01\x02\xff\xfe\xfd\xfc", "biner.xyz")


def test_fallback_kosong_tolak_ingesteror() -> None:
    with pytest.raises(IngestError):
        PlainTextParser().parse(b"   \n\t ", "kosong.xyz")


def test_markitdown_error_dibungkus_ingesteror() -> None:
    parser = MarkItDownParser()

    class Boom:
        def convert_stream(self, stream: Any) -> Any:
            msg = "konverter retak"
            raise RuntimeError(msg)

    parser._converter = cast(MarkItDown, Boom())  # pengujian internal — semantik unit test
    with pytest.raises(IngestError):
        parser.parse(b"x", "pecah.docx")


def test_router_dispatch_ekstensi_tak_kenal_ke_fallback() -> None:
    router = ParserRouter()
    parsed = router.parse(b"isi teks biasa", "fail.takkenal")
    assert parsed.blocks[0].text == "isi teks biasa"


def test_router_dispatch_ekstensi_terkenal_tak_gugur_fallback() -> None:
    """Ekstensi terdaftar tidak masuk jalur fallback (fallback menolak biner)."""

    router = ParserRouter()
    with pytest.raises(IngestError):  # PDF pecah → jalur PDF, bukan jalur teks
        router.parse(b"\x00\x01\x02", "fail.pdf")


def test_ekstensi_kosong_ataupun_tanpa_titik_tolak() -> None:
    router = ParserRouter()
    with pytest.raises(IngestError):
        router.parse(b"x", "")
    with pytest.raises(IngestError):
        router.parse(b"x", "tanpaekstensi")
