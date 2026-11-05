# AGENTS.md — rag-mvp

Malay-language RAG chatbot MVP. Stack: FastAPI + llama.cpp (Qwen3 1.7B GGUF, CPU-only) + pgvector Postgres 17 + Chainlit. See `docs/architecture.md` for the full analysis and `ROADMAP.md` (root) for the execution plan (the active sprint & status MUST be read before working).

## Golden rules

1. Python 3.12, strict typing (mypy `strict = true`). Every function annotated; no `Any` leaks.
2. Offline-first: no external LLM/embedding API calls in the app. Models are local files under `models/` (never committed, never baked into the Docker image).
3. `EMBED_DIM = 384` is locked. Changing the embedding model requires a schema migration + full re-index. Do not change it ad-hoc.
4. DB access only via `asyncpg`; SQL in repositories, never in route handlers. Every query parametrised — no string interpolation.
5. Grounded answers only: if retrieval returns no relevant chunks, the assistant must say so in Malay instead of hallucinating.
6. Never run `docker compose down -v` (destroys `pgdata`). Use `make down`.
6b. All commands (lint/test/e2e/eval/psql) run **inside the dev container** — the stage is selected via profile on ONE `docker-compose.yml`: `--profile dev` (tooling, code mounted) vs `--profile prod` (runtime image). Do not install/depend on tooling on the Windows host. The host only needs: Docker + make.
7. Never commit `.env`, `models/*.gguf|onnx`, or `data/inbox/*`. `.env.example` only.
8. Every chat response carries source citations (`source`, `chunk_index`). No citation = bug.
9. Chat, thinking, and structured output in Bahasa Melayu unless the user writes otherwise.
10. Observability: `structlog` JSON logs with `session_id`, `model`, `latency_ms`. No `print()`.
11. Roadmap discipline: work follows the sprints & KRs in `ROADMAP.md` (root); update the status (☐/🚧/✅) at the end of every session; new ideas → Backlog, never scope creep into the running sprint.

## Module map (planned) — Clean Architecture, all code in `src/`

Layers & dependency rule (inward): `adapters → application → domain`; `infrastructure` implements the ports; none of them import each other. See `src/README.md`.

| Path | Layer | Responsibility |
|---|---|---|
| `src/app/domain/` | domain | Pure entities & value objects: `chat.py`, `documents.py`, `errors.py` — no tech imports |
| `src/app/application/ports.py` | application | Protocols `Embedder`, `VectorRepository`, `Chunker`, `LLMGateway`, etc. |
| `src/app/application/use_cases/` | application | `ChatUseCase` (hybrid RRF + grounded + refusal), `IngestUseCase` (idempotent) |
| `src/app/infrastructure/postgres/` | infrastructure | asyncpg pool + the `VectorRepository` implementation (parametrised SQL) |
| `src/app/infrastructure/` | infrastructure | (sprints 1–3) onnx embedder, llama.cpp gateway, parser/chunker |
| `src/app/adapters/api.py` | adapters | FastAPI app factory, `/health`, `/chat`; HTTP → use case translation |
| `src/app/adapters/schemas.py` | adapters | Pydantic request/response (not domain) |
| `src/app/core/` | cross-cutting | `config.py` (Settings), `logging.py` (structlog), `container.py` (manual composition) |
| `src/eval/` | tooling | `run_eval.py` + golden dataset loader (consumes the adapters) |
| `tests/` (root) | tests | `unit` + `integration` (needs Docker) |
| `scripts/` | ops | `smoke.sh`, `backup.sh`, `restore.sh`, `mem_probe.sh` — shell, not Python code |

## Before you stop

Run the agent's quality gate (or the equivalent manually):

```
make check-all    # ruff + mypy + pytest -m unit
make smoke        # needs: docker compose up -d (app healthy)
```

## Skills

- `rag-dev` (`.opencode/skills/rag-dev/SKILL.md`) — change RAG behaviour: chunking, retrieval, prompts, evals.
- `pgvector-dev` (`.opencode/skills/pgvector-dev/SKILL.md`) — schema, index, migration, FTS config work.
- UI work (Sprint 1): follow `docs/conventions/ui.md` (Swiss Modernist) and test UI/UX changes via `tests/e2e/test_mockup.py` (`make test-e2e`).
