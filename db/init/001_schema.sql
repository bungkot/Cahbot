-- rag-mvp schema. Runs once via /docker-entrypoint-initdb.d on first boot.
-- Must be idempotent for fresh DBs only (initdb runs once). Do not assume re-runnable.
BEGIN;

-- Extension required for halfvec + FTS indexes.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ========== documents ==========
CREATE TABLE IF NOT EXISTS documents (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT NOT NULL UNIQUE,
    content_hash  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'indexed',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source);

-- ========== chunks ==========
-- EMBED_DIM is locked at 384 in schema. Changing embedding model requires
-- a migration. Record embedding model name in metadata JSONB per chunk.
CREATE TABLE IF NOT EXISTS chunks (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index  INT NOT NULL,
    content      TEXT NOT NULL,
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- halfvec(384): dimension locked; see docs/open-questions.md
    embedding    halfvec(384),
    -- FTS vector. Language config resolved dynamically below; see docs/data-model.md
    tsv          TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
);
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_chunk_index ON chunks(document_id, chunk_index);

-- HNSW index for dense retrieval (halfvec cosine). For <10k chunks, creating
-- this index after the first bulk load is often faster; it is created here
-- for a fresh DB so the FTS fallback path is still testable in CI.
-- NOTE: pgvector >= 0.7.0 required for halfvec + iterative scan.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON chunks USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- GIN index for sparse retrieval.
CREATE INDEX IF NOT EXISTS idx_chunks_tsv_gin
    ON chunks USING gin (tsv);

-- ========== ingest_jobs (job queue) ==========
CREATE TABLE IF NOT EXISTS ingest_jobs (
    id          BIGSERIAL PRIMARY KEY,
    path        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed
    attempts    INT NOT NULL DEFAULT 0,           -- retry counter (migrations/002)
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_status_created
    ON ingest_jobs(status, created_at);

-- ========== chat_sessions / chat_messages ==========
CREATE TABLE IF NOT EXISTS chat_sessions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,   -- 'user' | 'assistant'
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
    ON chat_messages(session_id, created_at);

COMMIT;

-- ============================================================================
-- FTS language config: 'indonesian' may not exist in every Postgres build.
-- Detect it; if missing, the schema falls back to 'simple'. The application
-- reads the resolved config from a file/key; see docs/data-model.md and
-- app/db.py. Verification: run  \dF  in psql and check for 'indonesian'.
-- ============================================================================
-- This block is intentionally best-effort and idempotent.
DO $$
DECLARE
    cfg TEXT;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'indonesian') THEN
        cfg := 'indonesian';
    ELSE
        cfg := 'simple';
    END IF;
    RAISE NOTICE 'FTS config resolved: %', cfg;
    -- Store resolved config in a lightweight table read by the app.
    CREATE TABLE IF NOT EXISTS _fts_config (name TEXT PRIMARY KEY, resolved TEXT NOT NULL);
    INSERT INTO _fts_config (name, resolved) VALUES ('fts_config', cfg)
        ON CONFLICT (name) DO UPDATE SET resolved = EXCLUDED.resolved;
END $$;

-- Alter generated column if 'indonesian' exists.
-- NOTE: Postgres cannot change a generated expression's language in-place
-- without rebuilding the index; for an MVP we recreate the column expression
-- only when the DB is empty (fresh init). If chunks already exist, run
-- `ALTER TABLE chunks ALTER COLUMN tsv SET DATA TYPE tsvector` + REINDEX
-- according to docs/runbook.md. This is a fresh-init script, so a direct
-- ALTER is safe here:
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'indonesian') THEN
        -- Rebuild generated column with Indonesian config (fresh DB: safe)
        ALTER TABLE chunks DROP COLUMN IF EXISTS tsv;
        ALTER TABLE chunks ADD COLUMN tsv TSVECTOR
            GENERATED ALWAYS AS (to_tsvector('indonesian', content)) STORED;
        DROP INDEX IF EXISTS idx_chunks_tsv_gin;
        CREATE INDEX idx_chunks_tsv_gin ON chunks USING gin (tsv);
        RAISE NOTICE 'FTS: using indonesian config';
    ELSE
        RAISE NOTICE 'FTS: indonesian config not found, keeping simple';
    END IF;
END $$;
