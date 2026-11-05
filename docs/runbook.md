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

## Sensitive operations (require reading the full runbook + backup)

- Changing the embedding model → procedure in `data-model.md`.
- Changing the FTS configuration while it holds data → rebuild tsvector + GIN inside a maintenance window.
- **Never**: `docker compose down -v`, DROP TABLE, TRUNCATE without backup.
