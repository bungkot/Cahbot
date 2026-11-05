---
name: pgvector-dev
description: Use this skill for schema, index, migration, or FTS config work on Postgres+pgvector
---

# pgvector development — rag-mvp

Sources of truth: `db/init/001_schema.sql`, `docs/data-model.md`, `docs/runbook.md`.

## Inviolable rules

1. `db/init/*.sql` is only for fresh DBs (a one-time initdb). Every subsequent change → `db/migrations/NNN_description.sql`, idempotent, recorded in the `_schema_migrations` table (create it if it doesn't exist yet, as part of migration 001 in migrations).
2. Never do directly: `docker compose down -v`, DROP TABLE, TRUNCATE without a backup (`make backup` first).
3. Verify performance changes with `EXPLAIN (ANALYZE, BUFFERS)` inside the db container:
   `docker compose exec db psql -U rag -d rag -c "EXPLAIN (ANALYZE, BUFFERS) ..."`.

## Indexing playbook

- < 10k chunks: load the data first, then `CREATE INDEX ... hnsw (embedding halfvec_cosine_ops) WITH (m=16, ef_construction=64)` — building after a bulk load is faster; for very large data set a high `maintenance_work_mem` on the session.
- HNSW does not guarantee full recall without a sufficient `hnsw.ef_search` — set it explicitly per connection (e.g. `SET hnsw.ef_search = 100`) and document the value.
- GIN tsvector for sparse; do not create a second tsvector column with a different config without a measured reason.

## halfvec vs vector

- `halfvec(384)` was chosen to save RAM/index size (fp16). Conversion: `embedding::vector(384)::real[]` is useful when debugging.
- Changing the embedding model (dim/model change) → the procedure in `docs/data-model.md`: chunks_v2 table → backfill embeddings → atomic swap → drop the old one → REINDEX.

## Malay FTS

- The corpus is Malay: Postgres has **no** `malay` dictionary — `_fts_config` must resolve to `simple` (the init schema already falls back automatically). If `indonesian` is present and chosen for its similar morphology, verify stemming quality on real Malay text first.
- Check availability: `SELECT * FROM pg_ts_config WHERE cfgname = 'indonesian';`
- The consolidated status lives in the `_fts_config` table (key `fts_config`). The application reads from there — do not hardcode `simple` or `indonesian` in Python.
- Switching the config on a DB holding data → rebuild the generated tsvector column + GIN index; run it in a maintenance window and plan the duration estimate.

## Tests

- Integration tests with the `integration` marker (need Docker + the pgvector container), using a disposable DB fixture (a schema template created per session, never modifying the main pgdata).
- Useful scripts: `make db-shell`, `make backup`, `make restore`.
