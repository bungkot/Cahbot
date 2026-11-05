"""Parser dokumen Office/markdown — MarkItDown (docx/xlsx/pptx/md/txt/csv/...)."""

from __future__ import annotations

import io

from markitdown import MarkItDown

from app.domain.documents import ParsedBlock, ParsedDocument, content_hash
from app.domain.errors import IngestError
from app.infrastructure.parsers.common import sanitize_text


class MarkItDownParser:
    """Implementasi `DocumentParser` untuk format non-PDF yang didukung MarkItDown."""

    def __init__(self) -> None:
        # Satu converter untuk seluruh file — biaya init tidak diulang per file.
        self._converter = MarkItDown()

    def parse(self, data: bytes, source: str) -> ParsedDocument:
        try:
            result = self._converter.convert_stream(io.BytesIO(data))
        except Exception as exc:
            msg = f"Konversi {source} gagal (MarkItDown)"
            raise IngestError(msg) from exc
        text = sanitize_text(result.markdown or result.text_content).strip()
        if not text:
            msg = f"{source} tidak berisi teks yang dapat diindeks"
            raise IngestError(msg)
        # Format non-PDF tidak berhalaman — satu blok, page=None.
        return ParsedDocument(
            source=source,
            content_hash=content_hash(data),
            blocks=(ParsedBlock(text=text),),
        )
