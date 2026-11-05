-- Migrasi 002: kolom `attempts` di ingest_jobs — penghitung retry worker.
-- Kebijakan (ROADMAP Sprint 2 KR-3): maksimum 2 percobaan per file sebelum
-- berstatus 'failed' final. Idempoten: aman dijalankan berulang.
BEGIN;

ALTER TABLE ingest_jobs
    ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0;

INSERT INTO _schema_migrations (migration) VALUES ('002_ingest_jobs_attempts.sql')
    ON CONFLICT (migration) DO NOTHING;

COMMIT;
