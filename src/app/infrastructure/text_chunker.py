"""Chunker token-based (infrastructure): ~512 token, overlap ~64.

Aturan (skill rag-dev — ingest): heading & tabel tidak pernah dipecah di
tengah. Unit atom = baris heading | 1 baris tabel | paragraf. Jika unit melebihi
target: tabel dipecah pada BATAS BARIS, paragraf pada BATAS KALIMAT — bukan
di karakter sembarang. Anggaran token: word-level deterministik (kata dipisah
spasi) — cukup untuk pemiawaian MVP tanpa tokenizer berat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.documents import Chunk, ParsedBlock, ParsedDocument, estimate_tokens

_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
DEFAULT_TARGET_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 64


@dataclass(slots=True)
class _Unit:
    """Unit atom hasil segmentasi markdown."""

    lines: list[str]
    page: int | None
    kind: str  # "heading" | "table" | "paragraph"
    heading_text: str | None = None


class TextChunker:
    """Implementasi `application.ports.Chunker` — atas dokumen hasil parser."""

    def __init__(
        self,
        *,
        target_tokens: int = DEFAULT_TARGET_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ) -> None:
        if not 0 < overlap_tokens < target_tokens:
            msg = "harus 0 < overlap_tokens < target_tokens"
            raise ValueError(msg)
        self._target = target_tokens
        self._overlap = overlap_tokens
        self._counter = 0

    def chunk(self, document: ParsedDocument) -> list[Chunk]:
        units = _build_units(document.blocks)
        self._counter = 0  # index chunk direstart per dokumen (kunci sitasi)
        return list(self._pack(units))

    # --- pengemas unit ------------------------------------------------------

    def _pack(self, units: list[_Unit]) -> list[Chunk]:
        chunks: list[Chunk] = []
        buffer: list[_Unit] = []
        buffer_tokens = 0
        buffer_is_pure_tail = False  # buffer hanya ekor-overlap (belum bertemu unit baru)
        heading_raw: str | None = None  # baris heading mentah (diprepends ke konten)
        heading_text: str | None = None  # teks bersih untuk metadata

        for unit in units:
            if unit.kind == "heading":
                if buffer and not buffer_is_pure_tail:
                    # Sempadan seksyen: ekor-overlap TIDAK menyeberangi heading
                    # (mengelak chunk pendua yang hanya berisi ekor).
                    chunks.append(self._make_chunk(buffer, heading_raw, heading_text))
                buffer, buffer_tokens = [], 0
                heading_raw, heading_text = unit.lines[0], unit.heading_text
                continue
            for piece in self._split_oversized(unit):
                piece_tokens = estimate_tokens("\n".join(piece.lines))
                if buffer and buffer_tokens + piece_tokens > self._target:
                    chunks.append(self._make_chunk(buffer, heading_raw, heading_text))
                    # heading aktif TIDAK di-reset — buffer tail adalah sambungan sama seksyen.
                    buffer, buffer_tokens = self._overlap_tail(buffer)
                    buffer_is_pure_tail = True
                buffer.append(piece)
                buffer_tokens += piece_tokens
                buffer_is_pure_tail = False
        if buffer and not buffer_is_pure_tail:
            chunks.append(self._make_chunk(buffer, heading_raw, heading_text))
        return chunks

    def _overlap_tail(self, buffer: list[_Unit]) -> tuple[list[_Unit], int]:
        """Ambil unit dari ekor buffer bernilai <= overlap token untuk konteks berikutnya.

        Overlap menyalin unit UTUH — menegakkan 'tidak membelah'. Bila unit terakhir
        sendirian melebihi overlap: paragraf dipotong pada EKOR KATA (duplikasi,
        bukan pemecahan); tabel/heading tidak — tail kosong supaya chunk tidak
        menduplikasi keseluruhan isi chunk sebelumnya.
        """
        tail: list[_Unit] = []
        total = 0
        for unit in reversed(buffer):
            t = estimate_tokens("\n".join(unit.lines))
            if total + t > self._overlap:
                break
            tail.append(unit)
            total += t
        if not tail:
            last = buffer[-1]
            if last.kind == "paragraph":
                words = "\n".join(last.lines).split()
                if words:
                    piece = " ".join(words[-self._overlap :])
                    tail.append(_Unit(lines=[piece], page=last.page, kind="paragraph"))
                    total = estimate_tokens(piece)
        tail.reverse()
        return tail, total

    def _make_chunk(
        self, units: list[_Unit], heading_raw: str | None, heading_text: str | None
    ) -> Chunk:
        lines: list[str] = ([heading_raw] if heading_raw else []) + [
            line for u in units for line in u.lines
        ]
        content = "\n".join(lines).strip()
        index = self._next_index()
        return Chunk(
            chunk_index=index,
            content=content,
            heading=heading_text,
            page=units[0].page,
            n_tokens=estimate_tokens(content),
        )

    def _next_index(self) -> int:
        val = self._counter
        self._counter += 1
        return val

    # --- pemecahan unit berukuran besar -------------------------------------

    def _split_oversized(self, unit: _Unit) -> list[_Unit]:
        if estimate_tokens("\n".join(unit.lines)) <= self._target:
            return [unit]
        if unit.kind == "table":
            return self._split_table(unit)
        return self._split_paragraph(unit)

    def _split_table(self, unit: _Unit) -> list[_Unit]:
        """Belah tabel HANYA pada batas baris (skill rag-dev: jangan pecah baris)."""
        groups: list[_Unit] = []
        current: list[str] = []
        current_tokens = 0
        for line in unit.lines:
            t = estimate_tokens(line)
            if current and current_tokens + t > self._target:
                groups.append(_Unit(lines=current, page=unit.page, kind="table"))
                current, current_tokens = [], 0
            current.append(line)
            current_tokens += t
        if current:
            groups.append(_Unit(lines=current, page=unit.page, kind="table"))
        return groups

    def _split_paragraph(self, unit: _Unit) -> list[_Unit]:
        """Belah paragraf pada batas kalimat; kalimat ekstrem → tingkap kata."""
        text = " ".join(unit.lines)
        sentences: list[str] = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
        if not sentences:
            sentences = [text]
        groups: list[_Unit] = []
        current: list[str] = []
        current_tokens = 0
        for sentence in sentences:
            for piece in _word_windows(sentence, self._target):
                t = estimate_tokens(piece)
                if current and current_tokens + t > self._target:
                    groups.append(
                        _Unit(lines=[" ".join(current)], page=unit.page, kind="paragraph")
                    )
                    current, current_tokens = [], 0
                current.append(piece)
                current_tokens += t
        if current:
            groups.append(_Unit(lines=[" ".join(current)], page=unit.page, kind="paragraph"))
        return groups


def _word_windows(text: str, max_tokens: int) -> list[str]:
    """Tingkap kata (kalimat ekstrem tanpa tanda baca) — tanpa overlap tambahan."""

    words = text.split()
    if not words:
        return []
    return [" ".join(words[i : i + max_tokens]) for i in range(0, len(words), max_tokens)]


def _build_units(blocks: tuple[ParsedBlock, ...]) -> list[_Unit]:
    """Segmentasi teks per halaman → senarai unit atom (heading/tabel/paragraf)."""

    units: list[_Unit] = []
    para: list[str] = []
    table: list[str] = []
    para_page: int | None = None
    table_page: int | None = None

    def flush_para() -> None:
        nonlocal para, para_page
        if para:
            units.append(_Unit(lines=para, page=para_page, kind="paragraph"))
            para, para_page = [], None

    def flush_table() -> None:
        nonlocal table, table_page
        if table:
            units.append(_Unit(lines=table, page=table_page, kind="table"))
            table, table_page = [], None

    for block in blocks:
        for line in block.text.splitlines():
            if not line.strip():
                flush_para()
                flush_table()
                continue
            stripped = line.strip()
            if _HEADING_RE.match(stripped):
                flush_para()
                flush_table()
                units.append(
                    _Unit(
                        lines=[stripped],
                        page=block.page,
                        kind="heading",
                        heading_text=stripped.lstrip("#").strip().lstrip(" ").strip(),
                    )
                )
            elif _TABLE_ROW_RE.match(line):
                flush_para()
                if not table:
                    table_page = block.page
                table.append(line.rstrip())
            else:
                flush_table()
                if not para:
                    para_page = block.page
                para.append(line.rstrip())
    flush_para()
    flush_table()
    return units
