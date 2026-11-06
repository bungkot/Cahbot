"""Unit: _ThinkDeltaFilter gateway — buang blok pemikiran Qwen3 dari aliran.

Tanda blok dibina per remitan (`"<" + "think>"`) sepanjang fail ini kerana
string literal tag terbelah oleh saluran kandungan.
"""

import pytest

from app.infrastructure.llama_cpp_gateway import _ThinkDeltaFilter

pytestmark = pytest.mark.unit

_OPEN = "<" + "think>"
_CLOSE = "<" + "/" + "think>"


def feed_all(deltas: list[str]) -> str:
    """Satu aliran delta → teks satu-nada (dengan flush di hujung)."""

    flt = _ThinkDeltaFilter()
    visible = [flt.feed(d) for d in deltas]
    visible.append(flt.flush())  # flush: end-of-stream
    return "".join(visible)


def test_blok_penuh_dalam_satu_delta_dibuang() -> None:
    """Blok penuh (buka + talar + tutup) dalam SATU delta — hanya jawapan."""

    text = feed_all([_OPEN + "langkah 1... langkah 2..." + _CLOSE + "12 hari."])
    assert text == "12 hari."


def test_tanda_pecah_lintas_delta() -> None:
    """Tanda buka pecah tiga delta — pemikiran tetap tak bocor."""

    text = feed_all([_OPEN[:3], _OPEN[3:] + "2 == 2", _CLOSE + "12 hari."])
    assert text == "12 hari."


def test_teks_sebelum_tanda_kekal() -> None:
    """Bila model tidak buka blok — teks atas-muka kekal intact."""

    text = feed_all(["Jawapan: ", "2 hari."])
    assert text == "Jawapan: 2 hari."


def test_blok_tanpa_tutup_semua_dibuang() -> None:
    """Aliran terputus saat masih menalar — flush tak memulangkan apa-apa."""

    flt = _ThinkDeltaFilter()
    assert flt.feed(_OPEN + "Sedang berfikir ") == ""
    assert flt.flush() == ""


def test_tanda_pecah_nomat() -> None:
    """Tag pembukaan terbelah (ekor buffer) — tiada bocor."""

    flt = _ThinkDeltaFilter()
    assert flt.feed(_CLOSE[:2]) == ""
    assert flt.feed(_CLOSE[2:] + "cukup") == "cukup"


def test_flush_menyatakan_sisa_bukan_pemikiran() -> None:
    """Ekor yang tertunggak (bukan tag) dilepaskan bila flush()."""

    flt = _ThinkDeltaFilter()
    out = flt.feed("Jawapan selesai <")
    assert out == "Jawapan selesai "
    assert flt.flush() == "<"
