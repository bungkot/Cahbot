"""Router parser: pilih implementasi `DocumentParser` berdasarkan ekstensi file."""

from __future__ import annotations

import fnmatch
import re

from app.domain.documents import ParsedDocument
from app.domain.errors import IngestError
from app.infrastructure.parsers.fallback_parser import PlainTextParser
from app.infrastructure.parsers.markitdown_parser import MarkItDownParser
from app.infrastructure.parsers.pdf_parser import PyMuPdfParser

_EXT_RE = re.compile(r"^\.[a-z0-9]{1,10}$")

# Urutan penting: pola paling spesifik dahulu (fnmatch, case-insensitive).
PDF_PATTERNS: tuple[str, ...] = ("*.pdf",)
MARKITDOWN_PATTERNS: tuple[str, ...] = (
    "*.docx",
    "*.xlsx",
    "*.pptx",
    "*.ppt",
    "*.doc",
    "*.xls",
    "*.md",
    "*.markdown",
    "*.txt",
    "*.csv",
    "*.json",
    "*.html",
    "*.htm",
)

EXTENSION_ERROR = "ekstensi file tidak valid"


class ParserRouter:
    """`DocumentParser` komposit: dispatch satu kali per file (hash dihitung di parser)."""

    def __init__(self) -> None:
        self._pdf = PyMuPdfParser()
        self._markitdown = MarkItDownParser()
        self._fallback = PlainTextParser()

    def parse(self, data: bytes, source: str) -> ParsedDocument:
        ext = _normalize_extension(source)
        if matches(ext, PDF_PATTERNS):
            return self._pdf.parse(data, source)
        if matches(ext, MARKITDOWN_PATTERNS):
            return self._markitdown.parse(data, source)
        return self._fallback.parse(data, source)


def matches(ext: str, patterns: tuple[str, ...]) -> bool:
    """fnmatch case-insensitive; ext sudah lowercase + titik di depan."""

    return any(fnmatch.fnmatch("*" + ext, pat) for pat in patterns)


def _normalize_extension(source: str) -> str:
    if not source:
        msg = EXTENSION_ERROR
        raise IngestError(msg)
    dot = source.rfind(".")
    ext = source[dot:].lower() if dot != -1 else ""
    if not _EXT_RE.match(ext):
        msg = EXTENSION_ERROR
        raise IngestError(msg)
    return ext
