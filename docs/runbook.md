# Runbook — rag-mvp

# Running the stack

All project commands run **inside the container** — the Windows host only runs Docker + make. ONE `docker-compose.yml`, stages selected via profile: `--profile dev` (db + tooling, code mounted) or `--profile prod` (db + runtime app image); service topology is identical.

```bash
cp .env.example .env          # adjust if needed
# place models on the host (mounted into the container):
#   models/Qwen3-1.7B-Q3_K_M.gguf        (LLM — kuantisasi q3_k_m)
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
| app keeps restarting, OOM | insufficient RAM (large model) | check `docker stats`; lower `LLM_N_CTX`/`LLM_N_BATCH`; make sure it's the q3_k_m 1.7B model (~939 MB); WSL VM bawaan ~4 GB tak cukup semasa load bersama embedder — naik `.wslconfig` (`memory=6GB, swap=4GB`) lalu restart Docker Desktop |
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

## Sprint 6 — Hardening evidence (2026-11-06)

### Boot-resume + graceful shutdown (KR-2)

- Job `running` yang tertinggal apabila proses mati kini dipulihkan otomatis
  saat startup (`requeue_stuck` di `PgIngestJobRepository`; dipanggil di
  lifespan SEBELUM worker mula). Bukti: job id 90 & 123 (`running` sisa crash
  Docker Desktop) kembali ke `pending` dan dituntaskan → `done` dalam
  log smoke penutupan yang sama.
- Shutdown: watchdog dibatalkan → pool asyncpg di-drain dengan had masa 10 s
  (`pool_closer` di Container; timeout → `pool_drain_timeout`, proses tetap
  keluar). Log `app_stop` TIDAK ditulis sebelum drain selesai.
- `/health` kini ping DB melalui pool (`health_ping_failed` → `database:
  unreachable`, status `degraded` — bukan HTTP 500).

### Auth & rate limit (KR-1)

| Env | Kesan |
|---|---|
| `API_KEY` (kosong = off) | route API (bukan `/health`) wajib header `X-API-Key` → 401 bila salah |
| `API_KEY_HEADER` | nama header (default `X-API-Key`) |
| `RATE_LIMIT_RPM` (0 = off) | sliding window 60 s per klien — kunci dasar IP; session_id (badan `/chat`) sekadar penanda kedua (rotasi sid oleh klien tidak mengelak had) → 429 |

`/health` kekal terbuka (load-balancer; websocket UI tidak membawa header).
oauth/oidca/Redis tidak diperlukan pada MVP — auth diproses di proses
sama (ADR D5); penskalaan melintang → Backlog.

### Watchdog rescue-scan (Backlog selesai)

`INGEST_RESCAN_INTERVAL_S` (default 15 s; 0 = off): setiap had waktu poll
yang lega, folder inbox dibaca semula dengan mtime-map in-memory — hanya fail
baru/berubah yang diantrekan. Menoleransi kelewatan event Watchdog pada mount
Windows→Linux; POST `/ingest` kekal sebagai laluan segera.

### RAM/latency profile (KR-3) — `make mem` (scripts/mem_probe.sh)

| Metrik | Nilai (n=4, host 8 GB, WSL 6 GB) |
|---|---|
| RSS uvicorn (embedder + GGUF yuklu) | 2002 MB |
| `/chat` latency p50 / p95 | ~27.3 s / ~34.8 s |
| retrieval-only p50/p95 (eval runner) | 15.6 / 20.4 ms |

Interpretasi: latensi didominasi generasi LLM 1.7B pada CPU (streaming ~0.7–1
tok/s); retrieval HNSW murah (ms). Pelarasan lanjut (n_batch, kuantisasi yang
lebih besar, threads lebih) dahulu diukur dengan `make mem` — jangan meneka.

### CORS Chainlit (Backlog selesai)

`.chainlit/config.toml` `allow_origins = ["http://localhost:8000"]` — wildcard
`["*"]` dibuang. Saat dilayani di origin lain, mengisi nilai origin
sebenar (bukan wildcard). Chainlit membenarkan asal-sama bila senarai kosong.

### Penolakan UI — strip merah (Backlog selesai)

Adapter membungkus pesan penolakan dalam markdown blockquote (`> …`) →
elemen `<blockquote>` — strip merah 2pt dari `public/swiss.css` (Chainlit
2.12 tidak memancarkan kelas `.refusal`). Pesan baku kekal verbatim; ujian
e2e mengaskan kehadiran blockquote.

### Troubleshooting — jurnal Sprint 6

| Simptom | Penyebab | Aksi |
|---|---|---|
| `/health` `database: unreachable` | pool asyncpg gagal ping | `docker compose logs db`; app tetap hidup (degraded) |
| uvicorn tidak hidup selepas kontainer dev restart | kontainer `sleep infinity` — app dijalankan manual | `docker exec -d -e PYTHONPATH=/workspace/src cahbot-dev-1 uv run uvicorn app.adapters.api:app --host 0.0.0.0 --port 8000` |
| `docker exec ... pkill -f uvicorn` membunuh shell sendiri | pola match baris arahan sendiri | pola `'[u]vicorn'` + jalankan kill & start pada exec berasingan |
| `ps -o args` terpotong bilamana arg panjang | procps truncating tanpa `-ww` | guna `ps -eww` atau `ps -eo comm,rss` |
| job `running` melekat | proses mati sebelum mark | kini otomatik (`requeue_stuck`); bila perlu manual: `UPDATE ingest_jobs SET status='failed', error='stuck' WHERE status='running';` |
| Docker Desktop engine 500 di tengah exec | engine flaking | `docker compose --profile dev up -d` semula + mula app manual; data selamat (pgdata volume) |
| RSS uvicorn membesar mendekati 2 GB | normal — GGUF 939 MB + KV cache n_ctx 4096 + ONNX | `make mem` untuk profil berkala; bukan symptom kecacatan |

## Sensitive operations (require reading the full runbook + backup)

- Changing the embedding model → procedure in `data-model.md`.
- Changing the FTS configuration while it holds data → rebuild tsvector + GIN inside a maintenance window.
- **Never**: `docker compose down -v`, DROP TABLE, TRUNCATE without backup.
