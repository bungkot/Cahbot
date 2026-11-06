"""Repo antrean ingest (infrastructure/postgres) — SQL parametrised $1..$n.

Aturan (skill pgvector-dev): polling worker memakai FOR UPDATE SKIP LOCKED agar
aman terhadap worker ganda. Retry dilakukan lewat kolom `attempts` (migrasi 002).
"""

from __future__ import annotations

import asyncpg
import structlog

from app.domain.documents import IngestJob, IngestJobStatus

logger = structlog.get_logger(__name__)

_ENQUEUE_SQL = """
INSERT INTO ingest_jobs (path, status)
SELECT $1, 'pending'
WHERE NOT EXISTS (
    SELECT 1 FROM ingest_jobs WHERE path = $1 AND status IN ('pending', 'running')
)
RETURNING id
"""

# Idempoten-antre: bila SELECT di atas tidak menghasilkan baris (sudah ada),
# kembalikan id job aktif yang sama supaya caller punya id deterministik.
_SELECT_ACTIVE_SQL = """
SELECT id FROM ingest_jobs
WHERE path = $1 AND status IN ('pending', 'running')
ORDER BY id DESC LIMIT 1
"""

_CLAIM_SQL = """
UPDATE ingest_jobs SET status = 'running', updated_at = now()
WHERE id = (
    SELECT id FROM ingest_jobs
    WHERE status = 'pending'
    ORDER BY created_at, id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
RETURNING id, path, status, attempts, error
"""

_MARK_DONE_SQL = """
UPDATE ingest_jobs SET status = 'done', error = NULL, updated_at = now()
WHERE id = $1
"""

# attempts+1; requeue ketika attempts < $2 (retry), lain failed final.
_MARK_FAILED_SQL = """
UPDATE ingest_jobs
SET attempts = attempts + 1,
    status = CASE WHEN attempts + 1 < $2 THEN 'pending' ELSE 'failed' END,
    error = $3,
    updated_at = now()
WHERE id = $1
RETURNING status = 'pending'
"""

_GET_SQL = "SELECT id, path, status, attempts, error FROM ingest_jobs WHERE id = $1"

_LIST_SQL = """
SELECT id, path, status, attempts, error FROM ingest_jobs
ORDER BY created_at DESC, id DESC
LIMIT $1
"""

# Sprint 6 KR-2: job `running` yang dibawa oleh proses yang MATI tidak akan
# pernah di-mark — kembalikan ke `pending` saat startup (attempts dikekalkan
# agar fail yang gagal berulang tumbang natural ke `failed`).
_REQUEUE_STUCK_SQL = """
UPDATE ingest_jobs
SET status = 'pending', error = $1, updated_at = now()
WHERE id IN (
    SELECT id FROM ingest_jobs WHERE status = 'running' FOR UPDATE SKIP LOCKED
)
RETURNING id
"""

_PING_SQL = "SELECT 1"


class PgIngestJobRepository:
    """Implementasi `application.ports.IngestJobStore` di atas tabel ingest_jobs."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def enqueue(self, path: str) -> int:
        async with self._pool.acquire() as conn:
            job_id: int | None = await conn.fetchval(_ENQUEUE_SQL, path)
            if job_id is None:
                job_id = await conn.fetchval(_SELECT_ACTIVE_SQL, path)
            if job_id is None:  # lomba enqueu oleh handler serentak (toleransi MVP)
                job_id = await conn.fetchval(
                    "INSERT INTO ingest_jobs (path) VALUES ($1) RETURNING id", path
                )
            return int(job_id)

    async def claim_pending(self) -> IngestJob | None:
        async with self._pool.acquire() as conn:
            row: asyncpg.Record | None = await conn.fetchrow(_CLAIM_SQL)
        if row is None:
            return None
        return self._to_job(row)

    async def mark_done(self, job_id: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_MARK_DONE_SQL, job_id)
        logger.info("job_marked_done", job_id=job_id)

    async def mark_failed_or_retry(self, job_id: int, error: str, *, max_attempts: int) -> bool:
        async with self._pool.acquire() as conn:
            requeued = await conn.fetchval(_MARK_FAILED_SQL, job_id, max_attempts, error[:500])
        logger.warning("job_failed", job_id=job_id, requeued=requeued, error=error[:500])
        return bool(requeued)

    async def get_job(self, job_id: int) -> IngestJob | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_GET_SQL, job_id)
        return self._to_job(row) if row is not None else None

    async def list_jobs(self, limit: int = 50) -> list[IngestJob]:
        async with self._pool.acquire() as conn:
            rows: list[asyncpg.Record] = await conn.fetch(_LIST_SQL, limit)
        return [self._to_job(r) for r in rows]

    async def requeue_stuck(self, reason: str) -> int:
        async with self._pool.acquire() as conn:
            rows: list[asyncpg.Record] = await conn.fetch(_REQUEUE_STUCK_SQL, reason[:500])
        count = len(rows)
        if count:
            logger.info("jobs_requeued_on_boot", recovered=count, reason=reason[:120])
        return count

    async def ping(self) -> bool:
        async with self._pool.acquire() as conn:
            val: int | None = await conn.fetchval(_PING_SQL)
        return val == 1

    def _to_job(self, row: asyncpg.Record) -> IngestJob:
        return IngestJob(
            id=int(row["id"]),
            path=str(row["path"]),
            status=IngestJobStatus(str(row["status"])),
            attempts=int(row["attempts"]),
            error=row["error"],
        )
