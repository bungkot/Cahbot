"""Bina korpus seed Bahasa Melayu (offline) ke `data/inbox/` — sumber golden set awal.

Korpus seed adalah DOKUMEN KONSTRUKTIF (bukan data organisasi sebenar). Fakta
dikunci konsisten dengan `eval/golden/golden.jsonl` — apabila korpus sebenar
menggantikan seed: re-ingest + tulis semula golden set (docs/eval.md).

Jalankan: docker compose --profile dev run --rm dev uv run python scripts/build_seed_corpus.py
Worker watchdog akan memproses fail baru secara automatik.
"""

from __future__ import annotations

import os
from typing import Final

import pymupdf

# Mount ./data → /data di container dev; di CI/host lain dioverride via env
# (sama dengan INGEST_INBOX_PATH aplikasi — satu sumber direktori inbox).
INBOX: Final = os.environ.get("INGEST_INBOX_PATH", "/data/inbox")


def _write_pdf(path: str, blocks: list[list[str]]) -> None:
    """Satu halaman per blok; blok = [tajuk, badan]. Per halaman teks ringkas."""

    doc = pymupdf.open()
    for _page_no, (title, body) in enumerate(blocks, start=1):
        page = doc.new_page()
        page.insert_text((72, 72), title, fontsize=18)
        y = 110
        for line in body.splitlines():
            page.insert_text((72, y), line, fontsize=11)
            y += 28
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    print(f"[seed] tulis {path}")


def main() -> None:
    # Direktori inbox wajib ada dulu (CI/host tidak selalu sudah punya —
    # hanya container dev yang di-mount dari data/ yang sudah ada).
    os.makedirs(INBOX, exist_ok=True)
    cuti = [
        [
            "DASAR CUTI KAKITANGAN",
            (
                "Cuti tahunan\n"
                "Kakitangan tetap layak menerima cuti tahunan sebanyak 12 hari bekerja\n"
                "bagi setiap tahun perkhidmatan, tertakluk kepada kelulusan ketua jabatan.\n"
                "Baki cuti tahunan boleh diwarisi ke tahun berikutnya sehingga 90 hari;\n"
                "melebihi 90 hari hanya boleh diluluskan oleh pengurusan atasan.\n"
            ),
        ],
        [
            "TAMBAHAN CUTI",
            (
                "Pegawai yang telah berkhidmat selama 5 tahun berterusan layak menerima\n"
                "tambahan 2 hari cuti tahunan. Hari tambahan ini tidak boleh dibawa ke\n"
                "tahun berikutnya.\n"
            ),
        ],
        [
            "CUTI SAKIT",
            (
                "Cuti sakit perlu disokong oleh sijil doktor yang sah. Sijil doktor harus\n"
                "diserahkan kepada ketua jabatan dalam tempoh 48 jam dari hari bertugas\n"
                "pertama selepas cuti sakit. Tanpa sijil doktor, hari tersebut dijadikan\n"
                "cuti tidak berbayar.\n"
            ),
        ],
        [
            "CUTI BERSALIN",
            (
                "Kakitangan wanita layak menerima cuti bersalin berbayar sebanyak 90 hari\n"
                "bagi setiap kali bersalin, selaras dengan akta kerja yang berkuat kuasa.\n"
            ),
        ],
    ]
    _write_pdf(f"{INBOX}/handbook-cuti.pdf", cuti)

    claim = [
        [
            "PANDUAN KLAIM PERJALANAN",
            (
                "Kadar\n"
                "Elaun perjalanan darat di dalam negeri dibayar pada kadar RM0.40 bagi\n"
                "setiap kilometer jarak perjalanan. Perjalanan udara tertakluk kepada\n"
                "kadar tiket ekonomi dan memerlukan kelulusan awal.\n"
            ),
        ],
        [
            "TEMPOH KLAIM",
            (
                "Borang klaim perlu dikemukakan dalam tempoh 14 hari bekerja dari tarikh\n"
                "perjalanan. Klaim yang dikemukakan selepas tempoh ini akan ditolak\n"
                "melainkan dengan persetujuan khas pengurusan kewangan.\n"
            ),
        ],
        [
            "KELULUSAN KLAIM",
            (
                "Setiap klaim perlu diluluskan oleh Ketua Jabatan sebelum diproses oleh\n"
                "Unit Kewangan. Resit asli perlu dilampirkan bersama borang klaim;\n"
                "resit salinan tidak diterima. Klaim dibayar pada bulan berikutnya\n"
                "selepas tarikh kelulusan.\n"
            ),
        ],
    ]
    _write_pdf(f"{INBOX}/panduan-klaim-perjalanan.pdf", claim)

    print("[seed] korpus seed siap")


if __name__ == "__main__":
    main()
