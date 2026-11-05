---
description: Continue scaffolding the FastAPI application per the built Clean Architecture
---

Work following `ROADMAP.md` (active sprint) and the module map + golden rules in `AGENTS.md`. The Clean Architecture skeleton already exists in `src/` — see `src/README.md` & `docs/conventions/code.md`. **Do not rewrite the structure; just extend the existing modules:**

1. `src/app/core/config.py` — pydantic-settings Settings reading every variable from `.env.example` (already exists; audit the coverage).
2. `src/app/infrastructure/postgres/` — asyncpg pool; read the FTS config from the `_fts_config` table (parametrised SQL).
3. `src/app/adapters/api.py` — app factory, GET `/health` (DB check + model file check), clean startup/shutdown.
4. `src/app/adapters/schemas.py` — ChatRequest/ChatResponse (citations), IngestStatus, SessionMessages.
5. Ports (`src/app/application/ports.py`) + the `documents`, `chunks`, `ingest_jobs`, `chat_sessions/messages` repositories from the SQL in `db/init/001_schema.sql` — the app only consumes through the ports.
6. Routers `/chat`, `/ingest`, `/sessions` (adapters) with stubs that trace the flow so the smoke test runs.
7. Unit tests for config & schemas; `unit` marker. Do not disturb the existing `src/app/domain/`.

All commands run in the container (`make up` → `make check-all`). Report the results.
