"""Guard penafian deterministik — soalan keadaan peribadi (pasca-Sprint 6).

Soalan yang menanya KEADAAN PERIBADI pengguna (baki cuti tinggal, baki klaim
belum dibayar) meminta data dinamik per-orang yang TIDAK PERNAH wujud dalam
korpus dasar statik → penolakan yakin tanpa LLM (golden rule #5; bukti
halusinasi borderline `g011`: docs/eval.md § Sprint 4 verification — model
1.7B menghalusinasi nombor dasar sebagai "baki" walaupun aturan lapisan prompt).

Kondisi yakin diukur atas golden set (tambah kes g021-g024):
- kata-ganti diri pertama ("saya"/"aku") DAN
- kata kuantiti-baki ("baki"/"tinggal") DAN
- BUKAN penanya dasar ("Bolehkah/Adakah/Apa/Bagaimana/Siapa...") — soalan
  dasar yang bersentuh "baki" tetap boleh-jawab dari korpus (mis. g014
  "Bolehkah baki cuti tahunan dibawa..."; varian peribadi dasar g024
  "Bolehkah baki cuti saya dibawa...") → dibiarlalu ke retrieval.

Fungsi murni — tanpa import infrastruktur; boleh diuji & dipakai ulang oleh
use case (jalur pertama, sebelum embed) dan runner eval (imitasi polisi sama).
"""

from __future__ import annotations

import re
from typing import Final

# Kata-ganti diri pertama: soalan menyatakan milikan/maklumat PENGGUNA sendiri.
_FIRST_PERSON: Final[frozenset[str]] = frozenset({"saya", "aku"})

# Kata kuantiti-sisa: menanya jumlah yang BERBAKI/tersisa untuk individu —
# data dinamik luar korpus dasar (entitlement/tetapan sahaja yang statik).
_BALANCE_WORDS: Final[frozenset[str]] = frozenset({"baki", "tinggal", "berbaki"})

# Penanya dasar: jawabannya ada dalam korpus walaupun frasa "baki" muncul
# (dasar bawa-baki, dasar pengiraan). Bila hadir, guard TIDAK menafikan.
_POLICY_INTERROGATIVES: Final[frozenset[str]] = frozenset(
    {
        "bolehkah",
        "adakah",
        "apakah",
        "bagaimanakah",
        "siapakah",
        "manakah",
        "boleh",
        "apa",
        "bagaimana",
        "siapa",
        "mana",
        "kenapa",
        "mengapa",
        "macam mana",
    }
)

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")


def is_personal_state_query(question: str) -> bool:
    """Kembalikan True bila soalan meminta keadaan peribadi (baki sendiri).

    Peraturan kata-TIDAK-penanya-dasar mengelak penolakan palsu atas soalan
    dasar yang dibuat peribadi ("Bolehkah baki cuti saya dibawa...") — kes itu
    masih boleh-jawab dari korpus (bukti: kes golden g024, kontrol jawab).
    """

    words = set(_WORD_RE.findall(question.casefold()))
    if not words & _FIRST_PERSON:
        return False
    has_balance = "baki" in words  # jelas — kuantiti sisa peribadi
    has_left = bool(words & frozenset({"tinggal", "berbaki"}))
    if not (has_balance or has_left):
        return False
    if not has_balance and "berapa" not in words:
        # "Tinggal" tanpa soalan kuantiti berisiko homonim
        # ("saya tinggal di Shah Alam" = menduduki) → jangan menolak.
        return False
    return not words & _POLICY_INTERROGATIVES
