"""Fixture integration: DB sekali-guna per-sesi (skill pgvector-dev — jangan pgdata utama).

Tidak pernah sentuh `rag` DB: buat `test_rag_<acak>`, terapkan DDL minimal yang
setara `db/init/001_schema.sql`, drop di akhir. Semua ujian integration menerima
marker `integration` (perlu komposisi `--profile dev` sedang hidup).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

DEFAULT_DSN = "postgresql://rag:rag@db:5432/rag"

# DDL minimal setara 001_schema.sql (tanpa DO-block FTS dinamis — pin 'simple').
DDL_STATEMENTS: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS documents (
        id            BIGSERIAL PRIMARY KEY,
        source        TEXT NOT NULL UNIQUE,
        content_hash  TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'indexed',
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        id           BIGSERIAL PRIMARY KEY,
        document_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        chunk_index  INT NOT NULL,
        content      TEXT NOT NULL,
        metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
        embedding    halfvec(384),
        tsv          TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_chunk_index ON chunks(document_id, chunk_index)",
    """
    CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
        ON chunks USING hnsw (embedding halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunks_tsv_gin ON chunks USING gin (tsv)",
    """
    CREATE TABLE IF NOT EXISTS ingest_jobs (
        id          BIGSERIAL PRIMARY KEY,
        path        TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'pending',
        attempts    INT NOT NULL DEFAULT 0,
        error       TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ingest_jobs_status_created ON ingest_jobs(status, created_at)",
    "CREATE TABLE IF NOT EXISTS _fts_config (name TEXT PRIMARY KEY, resolved TEXT NOT NULL)",
    "INSERT INTO _fts_config (name, resolved) VALUES ('fts_config', 'simple')",
)


def _database_dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DSN)


@pytest.fixture(scope="session")
def dsn() -> str:
    """DSN admin (database utama) — komposisi dev wajib hidup."""

    return _database_dsn()


@pytest.fixture
async def pg_db(dsn: str) -> AsyncIterator[str]:
    """DB ujian sekali-guna per-test; yield DSN koneksi ke DB sirewati itu."""

    akronim = hashlib.sha256(os.urandom(8)).hexdigest()[:8]
    db_name = f"test_rag_{akronim}"
    admin = await asyncpg.connect(dsn=dsn)
    try:
        # Identifier dibina fixture (acak), bukan input user — tidak dapat di-bind.
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    except Exception:
        await admin.close()
        raise
    target_dsn = dsn.rsplit("/", 1)[0] + "/" + db_name
    conn = await asyncpg.connect(dsn=target_dsn)
    try:
        for statement in DDL_STATEMENTS:
            await conn.execute(statement)
        yield target_dsn
    finally:
        await conn.close()
        # Identifier fixture hanya — aman.
        await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        await admin.close()
