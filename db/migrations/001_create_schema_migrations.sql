-- Migrasi 001: tabel catatan migrasi (wajib menurut skill pgvector-dev).
-- Setiap file db/migrations/NNN_*.sql mencatat dirinya di sini setelah dijalankan.
BEGIN;

CREATE TABLE IF NOT EXISTS _schema_migrations (
    migration  TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO _schema_migrations (migration) VALUES ('001_create_schema_migrations.sql')
    ON CONFLICT (migration) DO NOTHING;

COMMIT;
