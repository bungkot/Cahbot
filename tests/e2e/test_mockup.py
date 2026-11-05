"""E2E Playwright — mockup UI Cahbot (Swiss Modernist).

Menjalankan uji visi tampilan SEBELUM pipeline: mockup disajikan sebagai file
statis (file://). Jalankan: `make test-e2e` (butuh `playwright install chromium`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

MOCKUP = Path(__file__).resolve().parents[2] / "mockup" / "index.html"


@pytest.fixture
def page(page: Page) -> Iterator[Page]:
    page.goto(MOCKUP.as_uri())
    yield page


def test_masthead_shows_brand_and_empty_thread(page: Page) -> None:
    expect(page.get_by_role("heading", name="CAHBOT")).to_be_visible()
    # thread mulai kosong (use case baru); composer + dropzone siap pakai
    expect(page.locator("#chat-thread article")).to_have_count(0)
    expect(page.locator("#composer")).to_be_visible()
    expect(page.locator("#dropzone")).to_contain_text("Letakkan fail di sini")


def test_demo_grounded_appends_answer_with_citations(page: Page) -> None:
    # thread kosong -> jawaban grounded pertama = 1 artikel dengan sitasi (golden rule #8)
    page.click("#demo-grounded")
    assert page.locator("#chat-thread article").count() == 1
    expect(page.locator(".msg.assistant").last.locator(".citations li")).to_have_count(2)


def test_demo_refusal_shows_message_without_citations(page: Page) -> None:
    page.click("#demo-refusal")
    refusal = page.locator("#refusal-msg")
    expect(refusal).to_be_visible()
    expect(refusal.locator(".citations")).to_have_count(0)
    # strip merah di tepi kiri — aksen Swiss hanya untuk status exceptional
    expect(refusal).to_have_class("msg assistant refusal")


def test_composer_accepts_question_and_appends_thread(page: Page) -> None:
    page.fill("#question", "Bila batas pengajuan cuti?")
    page.click("#send")
    expect(page.locator(".msg.user").last).to_contain_text("batas pengajuan cuti")
    (expect(page.locator(".msg.assistant").last).to_contain_text("mockup"))
    # input dibersihkan setelah kirim
    expect(page.locator("#question")).to_have_value("")


def test_layout_is_responsive_on_narrow_viewport(page: Page) -> None:
    page.set_viewport_size({"width": 360, "height": 720})
    # sidebar disembunyikan < 760px; thread & composer tetap
    expect(page.locator("aside")).to_be_hidden()
    expect(page.locator("#composer")).to_be_visible()
    expect(page.locator("#chat-thread")).to_be_visible()
