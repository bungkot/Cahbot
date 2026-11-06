"""E2E Playwright — UI Chainlit Sprint 5 (KR-1) di-mount pada app FastAPI.

Berbeza dengan test_mockup.py (mockup statik `file://`), di sini UI hidup dari
sistem sebenar: http://dev:8000/ (dalam rangkaian compose) atau E2E_BASE_URL.
Jalankan: `make test-e2e` (butuh playwright chromium + app hidup).

Kontrak minimum (docs/ROADMAP.md Sprint 5 KR-1 + docs/conventions/ui.md):
- laman "/" memaparkan UI chat (composer Chainlit, bukan error/JSON);
- hantar soalan → jawapan bermuncul + kaki-bilang sitasi (`kaki-sitasi (mono):`
  + senarai sumber), ATAU penolakan baku tanpa senarai sitasi (luar korpus).
LLM sebenar perlu masa (~7-30 s) - timeout dilonggarkan.
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8000")
# NB: jangan guna `http://dev:8000` — Chromium kuat-HSTS atas TLD `.dev` →
# net::ERR_SSL_PROTOCOL_ERROR; curl membalas, Chrome tidak. Dalam container,
# uvicorn hidup di container sama → `localhost:8000` tepat.

# kontrak docs/conventions/ui.md — ayat baku penolakan (mesti muncul verbatim)
REFUSAL = "Maaf, saya tidak menjumpai maklumat tersebut dalam dokumen yang tersedia."

# jawapan + generasi 1.7B; render Chainlit + websocket: longgarkan
STREAM_TIMEOUT_MS = 120_000


def _page_ready(page: Page) -> None:
    """Buka `/`; app belum hidup → skip (e2e bukan gerbang hidup-mati app)."""

    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
    except Exception as exc:
        pytest.skip(f"app tidak capai di {BASE_URL}: {exc}")
    # Chainlit + websocket boot (ui_session_start): kotak teks sudah nampak
    # SEBELUM frontend re-render selesai — tunggu buffer supaya fill tidak
    # dihapus rerender. (bearing: diagnostik bergunekan buffer 2 s.)
    expect(page.get_by_role("textbox").first).to_be_visible(timeout=30_000)
    page.wait_for_timeout(2_500)


def _send(page: Page, question: str) -> None:
    """Hantar soalan lewat composer: fill → sahkan isi → Enter pada keyboard."""

    box = page.get_by_role("textbox").first
    for _ in range(3):
        box.fill(question)
        page.wait_for_timeout(300)
        if box.input_value() == question:
            break
    assert box.input_value() == question, "Composer Chainlit hilang input (rerender)"
    page.keyboard.press("Enter")


def test_ui_laman_root_menampilkan_chat_bukan_error(page: Page) -> None:
    _page_ready(page)
    # kontrak D6: logotype Cahbot — dipaparkan sebagai title & avatar alt
    assert "Cahbot" in page.title(), f"tajuk halaman salah: {page.title()!r}"
    # tiada error raw HTML/JSON (app hidup)
    expect(page.get_by_role("textbox").first).to_be_visible()


def _body_text(page: Page) -> str:  # helper debug: selalu boleh dipanggil
    return page.locator("body").inner_text()


def test_ui_jawap_dengan_kaki_sitasi(page: Page) -> None:
    _page_ready(page)
    _send(page, "Berapa hari cuti tahunan bagi staf tetap?")

    body = page.locator("body")
    answered = body.get_by_text("12 hari").first
    refused = body.get_by_text(REFUSAL).first
    try:
        answered.wait_for(state="visible", timeout=STREAM_TIMEOUT_MS)
    except Exception:
        pytest.fail(f"jawapan tak muncul; body: {_body_text(page)[-900:]}")

    if refused.is_visible():
        pytest.skip("model menolak di-skrip di luar jurang skor — bukan isu UI Sprint 5")

    # streaming berjalan — tunggu kaki-sitasi (marker tunggal end-of-stream)
    expect(body.get_by_text("kaki-sitasi (mono):").first).to_be_visible(timeout=STREAM_TIMEOUT_MS)
    content = _body_text(page)
    # kontrak golden #8: sitasi mesti muncul (sumber + kaki-bilang senarai)
    assert "handbook-cuti.pdf" in content, "sitasi sumber tiada dalam jawapan UI"
    assert "kaki-sitasi (mono):" in content, "senarai kaki-bilang tiada"


def test_ui_luar_korpus_ditolak_tanpa_sitasi(page: Page) -> None:
    _page_ready(page)
    _send(page, "Berapa subsidi internet pekerja setiap bulan?")

    body = page.locator("body")
    expect(body.get_by_text(REFUSAL).first).to_be_visible(timeout=STREAM_TIMEOUT_MS)
    content = body.inner_text()
    assert "kaki-sitasi (mono):" not in content, "penolakan TIDAK boleh membawa sitasi"
    # Sprint 6 (review Sprint 5): strip merah via blockquote — pesan penolakan
    # dibungkus `> ` oleh adapter → elemen <blockquote> yang digaya swiss.css.
    quote = page.locator("blockquote", has_text="Maaf")
    expect(quote.first).to_be_visible()
