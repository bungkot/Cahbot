"""Parser PDF — PyMuPDF4LLM (pertahankan struktur markdown per halaman)."""

from __future__ import annotations

import re
from typing import Any

import pymupdf
import pymupdf4llm

from app.domain.documents import ParsedBlock, ParsedDocument, content_hash
from app.domain.errors import IngestError
from app.infrastructure.parsers.common import sanitize_text


class PyMuPdfParser:
    """Implementasi `DocumentParser` untuk `application/pdf` (file lokal, offline)."""

    def parse(self, data: bytes, source: str) -> ParsedDocument:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")  # type: ignore[no-untyped-call]
        except Exception as exc:
            msg = f"PDF tidak dapat dibuka: {source}"
            raise IngestError(msg) from exc
        blocks: list[ParsedBlock] = []
        try:
            # page_chunks=True → satu blok per halaman (metadata page tersedia).
            pages: list[dict[str, Any]] = pymupdf4llm.to_markdown(doc, page_chunks=True)
        except Exception as exc:
            doc.close()  # type: ignore[no-untyped-call]
            msg = f"Ekstraksi teks PDF gagal: {source}"
            raise IngestError(msg) from exc
        doc.close()  # type: ignore[no-untyped-call]
        for chunk in pages:
            # pymupdf4llm kadang menggandakan penanda heading ('# # Judul') —
            # normalisasi ke satu '#' agar metadata heading bersih.
            raw = re.sub(r"^(#{1,6}\s+)+", "# ", str(chunk.get("text", "")), flags=re.MULTILINE)
            text = sanitize_text(raw).strip()
            if not text:
                continue  # halaman kosong (sampul/gambar) → dilewati
            meta = chunk.get("metadata", {}) or {}
            # pymupdf4llm page_chunks: nomor halaman ada di 'page_number' (1-based).
            page = int(meta.get("page_number", 0) or 0)
            blocks.append(ParsedBlock(text=text, page=page if page > 0 else None))
        if not blocks:
            msg = f"PDF tidak berisi teks yang dapat diindeks: {source}"
            raise IngestError(msg)
        return ParsedDocument(source=source, content_hash=content_hash(data), blocks=tuple(blocks))
