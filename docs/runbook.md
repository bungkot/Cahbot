# Runbook — rag-mvp

# Running the stack

All project commands run **inside the container** — the Windows host only runs Docker + make. ONE `docker-compose.yml`, stages selected via profile: `--profile dev` (db + tooling, code mounted) or `--profile prod` (db + runtime app image); service topology is identical.

```bash
cp .env.example .env          # adjust if needed
# place models on the host (mounted into the container):
#   models/qwen3-1.7b-q4_k_m.gguf        (LLM)
#   models/multilingual-e5-small.onnx    (embedding)

make up                       # dev stack: db + dev container (deps + chromium ready)
make playwright-install        # once only (idempotent)
make test-e2e                  # pytest -m e2e in the container
make check-all                 # lint/mypy/unit in the container
make db-shell                  # psql in the db container
# production deploy: make up-prod (uses docker-compose.yml)
```

Production UI: Chainlit at `http://localhost:8000/` (arriving with the Sprint 5 mount). Visual mockup: open `mockup/index.html` directly or serve it statically.

## Everyday commands

| Command | Purpose |
|---|---|
| `make check-all` | lint + typecheck + unit tests |
| `make smoke` | test the running stack end-to-end |
| `make db-shell` | interactive psql |
| `make backup` / `make restore` | pg_dump / pg_restore to `backups/` |
| `make eval` | RAG quality evaluation |

## Troubleshooting guide

| Symptom | Common cause | Action |
|---|---|---|
| dev container fails to build during apt | stale repo cache / network | `docker compose --profile dev build --no-cache dev` |
| Playwright "host system missing deps" | forgot `playwright install --with-deps chromium` | `make playwright-install` |
| e2e crash: /dev/shm full | chromium headless OOM in shm | use the `--disable-dev-shm-usage` flag; check the dev `mem_limit` |
| `make: command not found` (Windows) | make not installed on the host | run the underlying command directly: `docker compose --profile dev run --rm dev uv run pytest -m unit` — Makefile targets are just wrappers |
| mounted files show CRLF / odd permissions | git autocrlf / mount attributes | use `.gitattributes` with `* text=auto eol=lf`; check `git config core.autocrlf false` |
| app keeps restarting, OOM | insufficient RAM (large model) | check `docker stats`; lower `LLM_N_CTX`/`LLM_N_BATCH`; make sure it's the q4 1.7B model |
| `/health` fails after up | DB not healthy yet | `docker compose logs db`; the healthcheck waits automatically |
| answer without citations | empty retrieval or broken prompt | check the chunk count in the logs; run a manual retrieval query in db-shell |
| ingest not running | watchdog/worker dead | check `ingest_jobs` row statuses; `make logs` |
| HNSW slow/expensive | `ef_search` too big, index missing | `EXPLAIN ANALYZE`; make sure the index is created after a large bulk load |

## Indexing playbook (Sprint 3)

Correct order per the pgvector-dev skill — HNSW builds faster after a bulk load:

1. `make backup` before any risky DB operation.
2. Bulk load the corpus (`make seed` → worker auto-ingests, or drop files into `data/inbox/`).
3. Create the HNSW index if it is not there yet (fresh DBs already have it from `db/init/001_schema.sql`):

   ```sql
   SET maintenance_work_mem = '128MB';
   CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
       ON chunks USING hnsw (embedding halfvec_cosine_ops)
       WITH (m = 16, ef_construction = 64);
   ```

4. Verify both indexes are actually used with `EXPLAIN (ANALYZE, BUFFERS)` inside the db container:

   ```bash
   docker compose exec db psql -U rag -d rag -c \
     "SET hnsw.ef_search=100; EXPLAIN (ANALYZE, BUFFERS) SELECT chunk_index FROM chunks ORDER BY embedding <=> $1::halfvec LIMIT 4;"
   ```

   - dense must show `Index Scan using idx_chunks_embedding_hnsw`;
   - sparse must show GIN (`idx_chunks_tsv_gin`) — only after enough rows (>~100) show up in the plan.
5. `hnsw.ef_search = 100` is set **per connection** by `PgVectorRepository` (no global SET). Do not rely on a default.
6. Record the EXPLAIN result in this runbook (section below) as part of the sprint evidence.

### Sprint 3 EXPLAIN evidence

Corpus: 7 chunks (seed corpus). `hnsw.ef_search=100` set per connection by the app.

**Dense — HNSW used, even without hints at tiny scale:**

```text
SET enable_seqscan=off;
EXPLAIN (ANALYZE, BUFFERS) SELECT chunk_index FROM chunks WHERE embedding IS NOT NULL
ORDER BY embedding <=> (SELECT embedding FROM chunks ORDER BY id LIMIT 1) LIMIT 4;
-> Index Scan using idx_chunks_embedding_hnsw on chunks
   Order By: (embedding <=> (InitPlan 1).col1)
   Buffers: shared hit=34
   Execution Time: 0.348 ms
```

Without `enable_seqscan=off` (default settings, 7 rows) the plan is still
`Index Scan using idx_chunks_embedding_hnsw` — 0.119 ms.//# (nearest-neighbour
ORDER BY <=> has no seq-scan shortcut at any size.)

**Sparse — GIN used (seqscan wins at 7 rows without the hint):**

```text
SET enable_seqscan=off;
EXPLAIN (ANALYZE, BUFFERS) SELECT chunk_index, ts_rank(tsv, plainto_tsquery('indonesian', 'cuti tahunan')) ...
-> Bitmap Index Scan on idx_chunks_tsv_gin (Index Cond: tsv @@ ...)
   Buffers: shared hit=4
   Execution Time: 0.405 ms
```

Without the hint at 7 rows the planner picks Seq Scan (expected — GIN only
pays off as the table grows; re-verify after a real bulk load).
FTS config from `_fts_config` = `indonesian` (this image ships it; the app
binds it as `$1::regconfig` — fully parametrised).

**Eval (Sprint 3, retrieval-only)**: `eval/results/20261106-004153.json` —
recall@4 = 1.00 (10/10), MRR@4 = 1.00, latency p50/p95 = 39/122 ms,
refusal-tanpa-konteks 0/3 (expected before the Sprint 4 RRF-threshold policy).

## Sensitive operations (require reading the full runbook + backup)

- Changing the embedding model → procedure in `data-model.md`.
- Changing the FTS configuration while it holds data → rebuild tsvector + GIN inside a maintenance window.
- **Never**: `docker compose down -v`, DROP TABLE, TRUNCATE without backup.
