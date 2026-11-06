"""Ingest korpus seed langsung ke DB (tanpa Docker/watchdog) — untuk CI.

Rangkaian gerbang GitHub Actions tidak punya container dev: worker watchdog
hanya hidup di lifespan app. Di job eval kosong kami TIDAK boot app — cukup
panggil `IngestUseCase` via container komposisi produksi (path logika sama
dengan HTTP /ingest; `core/container.build_production_container`). Korpus di
bangun dulu oleh `scripts/build_seed_corpus.py` (step terpisah di workflow).

Pemakaian (repo root, PYTHONPATH=src — seperti `make eval`):
    uv run python scripts/ci_seed_db.py

Env: DATABASE_URL, INGEST_INBOX_PATH, EMBED_* — dibaca lewat Settings
(pydantic-settings). Offline-first tetap: PDF seed dibuat lokal, tanpa API.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import get_settings
from app.core.container import build_production_container


async def _ingest_seed() -> None:
    """Ingest SETIAP PDF seed ke DB utama CI — sleep antar file tidak perlu."""

    settings = get_settings()
    container = await build_production_container(settings)
    try:
        inbox = Path(settings.ingest_inbox_path)
        pdfs = sorted(inbox.glob("*.pdf"))
        if not pdfs:
            raise SystemExit(f"[ci-seed] tidak ada PDF seed di {inbox}")
        for pdf in pdfs:
            # Source = nama file (setara path rel-posix inbox root yang
            # dipakai watchdog) — wajib cocok dgn golden.jsonl (docs/eval.md).
            result = await container.ingest.execute(pdf.read_bytes(), pdf.name)
            print(f"[ci-seed] {result.source}: {result.n_chunks} chunk(s)")
    finally:
        if container.pool_closer is not None:
            await container.pool_closer()


def main() -> None:
    asyncio.run(_ingest_seed())


if __name__ == "__main__":
    main()
