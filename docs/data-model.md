# Data model — rag-mvp

Source-of-truth schema: `db/init/001_schema.sql` (fresh-init; ongoing changes → `db/migrations/`).

## Tables

| Table | Purpose | Critical points |
|---|---|---|
| `documents` | one row per indexed file | `source UNIQUE`; `content_hash` for ingest idempotency |
| `chunks` | text chunk + embedding + tsv | `halfvec(384)` **locked**; `tsv` generated (config read from `_fts_config`) |
| `ingest_jobs` | ingest job queue | status `pending→running→done/failed`; index `(status, created_at)` |
| `chat_sessions` / `chat_messages` | conversation history | cascade delete per session |
| `_fts_config` | consolidated FTS language config | read by the application; never hardcode in Python |

## Embeddings & model migration

`EMBED_DIM=384` was chosen for `multilingual-e5-small`. Switching the embedding model means: create `chunks_v2` → backfill embeddings for all documents → atomic swap in a single transaction → drop the old table → REINDEX HNSW → update `.env` (`EMBED_DIM`, `EMBED_MODEL_PATH`) → run the full eval again. Do not ALTER the column in place.

## Index report

| Index | For | Notes |
|---|---|---|
| `idx_chunks_embedding_hnsw` (halfvec_cosine, m=16, ef=64) | dense search | set `hnsw.ef_search=100` explicitly per connection |
| `idx_chunks_tsv_gin` | sparse search | follows the `tsv` language config |
| `idx_ingest_jobs_status_created` | worker polling | |
| `idx_chat_messages_session_created` | history loading | |

## Chunk metadata convention (JSONB)

```json
{
  "embed_model": "multilingual-e5-small",
  "lang": "ms",
  "heading": "Bahagian 3 — Dasar Cuti",
  "page": 12,
  "chunk_size_tokens": 501
}
```

## Backup / restore

`make backup` (custom pg_dump into `backups/`, mandatory before any risky full migration) and `make restore`. The `pgdata` volume persists across restarts — use only `docker compose down` (never with `-v`).
