"""Pembersihan teks bersama untuk seluruh parser (skill rag-dev: normalize Malay text)."""

from __future__ import annotations

import re

# Kontrol C0/C1 dihapus KECUALI \n \t — angka & satuan tidak disentuh.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize_text(text: str) -> str:
    """Buang karakter kontrol; pertahankan \n \t, angka, satuan & tanda baca."""

    return _CONTROL_RE.sub("", text)
