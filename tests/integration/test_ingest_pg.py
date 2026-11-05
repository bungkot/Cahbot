"""Integration: ingest e2e terhad Postgres+pgvector sebenar (1 PDF kecil + idempotensi).

Fokus KR-5 Sprint 2: dokumen benar-benar terindeks (metadata heading/page),
idempotensi hash, antrean ingest_jobs + retry. Embedder diganti pengganti
deterministik (tanpa ONNX) — dimensi identik 384 (golden rule #3).
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import asyncpg
import pymupdf
import pytest

from app.adapters.worker.watchdog_runner import InboxWatchdogRunner
from app.application.use_cases.ingest import IngestUseCase
from app.domain.documents import IngestJobStatus
from app.infrastructure.parsers.router import ParserRouter
from app.infrastructure.postgres.jobs import PgIngestJobRepository
from app.infrastructure.postgres.repository import PgVectorRepository
from app.infrastructure.text_chunker import TextChunker

pytestmark = pytest.mark.integration


# --- pengganti embedder deterministik (tanpa ONNX — fokus ujian pada DB) --------


class DeterministicEmbedder:
    """Vektor 384d dari hash teks — L2-normalisasi; embed_model 'test-model'."""

    @property
    def model_name(self) -> str:
        return "test-model"

    async def warm_up(self) -> None:
        return None

    async def embed_query(self, text: str) -> list[float]:
        return _normalized_vector(text)

    async def embed_passage(self, text: str) -> list[float]:
        return _normalized_vector(text)


def _normalized_vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest() * 16  # >= 384 bait
    raw = [float(k % 256) / 255.0 - 0.5 for k in digest[:384]]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


def _pdf_bytes(first_text: str = "# Dasar Cuti") -> bytes:
    """PDF 2 halaman buatan (offline) — heading + kalimat per halaman."""
    doc = pymupdf.open()  # type: ignore[no-untyped-call]  # stub pymupdf parsial
    page1 = doc.new_page()
    page1.insert_text((72, 72), first_text)
    page1.insert_text((72, 100), "Cuti tahunan sebanyak 14 hari bagi setiap staf tetap.")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "# Prosedur Cuti Sakit")
    page2.insert_text((72, 100), "Sijil doktor diperlukan bila cuti melebihi dua hari.")
    return doc.tobytes(garbage=3, deflate=True)  # type: ignore[no-untyped-call,no-any-return]


async def _ingest_use_case(pg_db: str) -> tuple[IngestUseCase, PgIngestJobRepository, asyncpg.Pool]:
    vector_repo = await PgVectorRepository.create_from_env(pg_db, 1, 2)
    use_case = IngestUseCase(
        parser=ParserRouter(),
        chunker=TextChunker(),
        embedder=DeterministicEmbedder(),
        vector_repo=vector_repo,
        embed_batch_size=2,
    )
    jobs = PgIngestJobRepository(vector_repo.pool)
    return use_case, jobs, vector_repo.pool


# --- bahagian 1: pipeline parse → chunk → upsert ------------------------------


async def test_ingest_e2e_pdf_ke_chunk_metadata_wajib(pg_db: str) -> None:
    use_case, _jobs, pool = await _ingest_use_case(pg_db)
    result = await use_case.execute(_pdf_bytes(), "uji.pdf")
    async with pool.acquire() as conn:
        stored_hash = await conn.fetchval(
            "SELECT content_hash FROM documents WHERE source = $1", "uji.pdf"
        )
        n_chunks = int(await conn.fetchval("SELECT COUNT(*) FROM chunks"))
        rows = await conn.fetch("SELECT chunk_index, metadata FROM chunks ORDER BY chunk_index")
    assert stored_hash == result.content_hash
    assert n_chunks == result.n_chunks >= 2
    for row in rows:
        meta = row["metadata"]
        assert meta["embed_model"] == "test-model"
        assert meta["lang"] == "ms"
        assert meta["heading"] is not None  # kedua bahagian berserta heading
        assert meta["page"] is not None  # metadata page wajib ada untuk PDF
        assert meta["chunk_size_tokens"] is not None
    await pool.close()


async def test_carian_teks_sparse_dense_mengesan_chunk(pg_db: str) -> None:
    use_case, _jobs, pool = await _ingest_use_case(pg_db)
    await use_case.execute(_pdf_bytes(), "uji.pdf")
    repo = PgVectorRepository(pool, fts_config="simple")
    sparse_hits = await repo.search_sparse("cuti tahunan staf tetap", 4)
    assert len(sparse_hits) >= 1
    chunks = await repo.fetch_chunks(sparse_hits)
    assert any("staf tetap" in content for _docid, _src, content, _idx in chunks)
    dense_hits = await repo.search_dense(_normalized_vector("cuti tahunan"), 4)
    assert len(dense_hits) >= 1
    await pool.close()


async def test_idempotensi_bytes_sama_tidak_menduplikasi(pg_db: str) -> None:
    use_case, _jobs, pool = await _ingest_use_case(pg_db)
    data = _pdf_bytes()
    await use_case.execute(data, "uji.pdf")
    await use_case.execute(data, "uji.pdf")  # hash sama → no-op
    async with pool.acquire() as conn:
        n_chunks = int(await conn.fetchval("SELECT COUNT(*) FROM chunks"))
        n_docs = int(await conn.fetchval("SELECT COUNT(*) FROM documents"))
    assert n_docs == 1
    assert n_chunks > 0  # tepat sekali senarai chunk terkandung
    await pool.close()


async def test_hash_berbeza_mengganti_chunk_lama(pg_db: str) -> None:
    use_case, _jobs, pool = await _ingest_use_case(pg_db)
    await use_case.execute(_pdf_bytes(), "uji.pdf")
    async with pool.acquire() as conn:
        old_ids = {int(r[0]) for r in await conn.fetch("SELECT id FROM chunks")}
    await use_case.execute(_pdf_bytes(first_text="# Versi Kedua Berbeza Sepenuhnya"), "uji.pdf")
    async with pool.acquire() as conn:
        new_ids = {int(r[0]) for r in await conn.fetch("SELECT id FROM chunks")}
        n_docs = int(await conn.fetchval("SELECT COUNT(*) FROM documents"))
    assert n_docs == 1  # source unik — sekali baris
    assert new_ids.isdisjoint(old_ids)  # chunk lama digugurkan, baris baru
    await pool.close()


# --- bahagian 2: antrean job (ingest_jobs) ----------------------------------


async def test_antrean_jobs_requeue_lalu_failed(pg_db: str) -> None:
    _use_case, jobs, pool = await _ingest_use_case(pg_db)
    job_id = await jobs.enqueue("karat.xyz")
    first = await jobs.claim_pending()
    assert first is not None and first.id == job_id
    assert first.status == IngestJobStatus.RUNNING
    assert await jobs.mark_failed_or_retry(job_id, "gagal pertama", max_attempts=2) is True
    second = await jobs.claim_pending()
    assert second is not None and second.attempts == 1
    assert await jobs.mark_failed_or_retry(job_id, "gagal kedua", max_attempts=2) is False
    final = await jobs.get_job(job_id)
    assert final is not None and final.status == IngestJobStatus.FAILED
    assert final.error == "gagal kedua"
    assert await jobs.claim_pending() is None  # tiada pending terbaki
    listed = await jobs.list_jobs(10)
    assert any(j.id == job_id for j in listed)
    await pool.close()


# --- bahagian 3: worker runner alur e2e di atas DB sebenar --------------------


def _runner(
    use_case: IngestUseCase, jobs: PgIngestJobRepository, inbox: str
) -> InboxWatchdogRunner:
    return InboxWatchdogRunner(
        ingest=use_case,
        job_store=jobs,  # pyright: ignore[reportArgumentType]
        inbox_root=inbox,
        poll_interval_s=0.01,
        max_attempts=2,
    )


async def test_worker_job_dokumen_berjaya_hingga_done(pg_db: str, tmp_path: Any) -> None:
    use_case, jobs, pool = await _ingest_use_case(pg_db)
    inbox = Path(tmp_path)
    (inbox / "nota.txt").write_text("Nota perkhidmatan awam ditulis dalam file teks biasa.")
    await jobs.enqueue("nota.txt")
    job = await jobs.claim_pending()
    assert job is not None
    await _runner(use_case, jobs, str(inbox))._process(job)
    final = await jobs.get_job(job.id)
    assert final is not None and final.status == IngestJobStatus.DONE
    async with pool.acquire() as conn:
        n_chunks = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE d.source = $1",
                "nota.txt",
            )
        )
    assert n_chunks >= 1
    await pool.close()


async def test_worker_job_file_biner_gagal_lalu_failed(pg_db: str, tmp_path: Any) -> None:
    use_case, jobs, pool = await _ingest_use_case(pg_db)
    inbox = Path(tmp_path)
    (inbox / "binar.xyz").write_bytes(b"\x00\x01\xff\xfe\xfd\xfc\xfb\xfa")
    await jobs.enqueue("binar.xyz")
    first = await jobs.claim_pending()
    assert first is not None
    await _runner(use_case, jobs, str(inbox))._process(first)
    requeued = await jobs.get_job(first.id)
    assert requeued is not None and requeued.status == IngestJobStatus.PENDING
    second = await jobs.claim_pending()
    assert second is not None
    await _runner(use_case, jobs, str(inbox))._process(second)
    final = await jobs.get_job(second.id)
    assert final is not None and final.status == IngestJobStatus.FAILED
    assert final.error is not None
    await pool.close()
