"""Fallback parser — ekstensi tak dikenal: perlakukan sebagai teks biasa."""

from __future__ import annotations

from app.domain.documents import ParsedBlock, ParsedDocument, content_hash
from app.domain.errors import IngestError
from app.infrastructure.parsers.common import sanitize_text


class PlainTextParser:
    """Fallback: coba dekod teks; file biner → IngestError (job berstatus failed)."""

    def parse(self, data: bytes, source: str) -> ParsedDocument:
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = f"Format tidak dikenal & bukan teks UTF-8: {source}"
            raise IngestError(msg) from exc
        text = sanitize_text(raw).strip()
        if not text:
            msg = f"{source} tidak berisi teks yang dapat diindeks"
            raise IngestError(msg)
        return ParsedDocument(
            source=source,
            content_hash=content_hash(data),
            blocks=(ParsedBlock(text=text),),
        )
