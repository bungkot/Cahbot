# Cahbot - Retrieval Augmented Generation

**Malay-language** RAG chatbot, offline-first (CPU-only): FastAPI + llama.cpp (Qwen3 1.7B GGUF) + Postgres 17 + pgvector + Chainlit UI. All documents & models stay local — no external APIs.

## Quick start

```bash
cp .env.example .env
# download models into models/ (see models/README.md)
make up                     # dev: db + dev container (ONE compose file, dev profile)
make playwright-install     # once
make test-e2e               # UI mockup e2e in chromium (Sprint 1 active)
make check-all              # lint + mypy + unit — all in the container
# production:
make up-prod                # db + runtime app image (prod profile)
```

Drop documents into `data/inbox/` — ingest is automatic (watchdog, after Sprint 2). The Chainlit/custom UI decision comes from the Sprint 1 mockup (`mockup/index.html`). All commands run **inside the container** — on Windows the host only needs Docker + make (`docs/runbook.md`).

## Structure

All Python code lives in `src/` (src layout, Clean Architecture — dependency rule points inward):

```text
src/
├── app/
│   ├── domain/            pure entities (chat, documents, errors)
│   ├── application/       ports (Protocol) + use_cases (chat, ingest)
│   ├── infrastructure/    asyncpg/pgvector repo, (sprints 1–3: ONNX, llama.cpp, parser)
│   ├── adapters/          FastAPI (api.py, schemas.py), watchdog, Chainlit
│   └── core/              config, structlog logging, composition container
└── eval/                  run_eval.py + golden set loader
```

Details: `src/README.md` and `docs/conventions/code.md`.

## Documentation

| Doc | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Architecture decisions (ADR), components, data flow, sprint plan |
| [`ROADMAP.md`](ROADMAP.md) | **ROADMAP to completion** (sprints, KRs, definition of done, backlog) |
| [`docs/data-model.md`](docs/data-model.md) | pgvector schema, indexes, embedding model migration |
| [`docs/retrieval.md`](docs/retrieval.md) | Hybrid retrieval + RRF, parameters, citation/refusal policy |
| [`docs/eval.md`](docs/eval.md) | Golden set, metrics & thresholds, how to run the eval |
| [`docs/runbook.md`](docs/runbook.md) | Daily operations, troubleshooting, sensitive operations |
| [`docs/conventions/code.md`](docs/conventions/code.md) | Python code conventions + Clean Architecture |
| [`docs/conventions/ui.md`](docs/conventions/ui.md) | **Swiss Modernist** design system (typography, grid, color, components) |
| [`mockup/README.md`](mockup/README.md) | UI mockup + Playwright e2e (Sprint 1 — UI vision first) |
| [`AGENTS.md`](AGENTS.md) | Golden rules for the AI agent (OpenCode) |

## OpenCode harness (quality via AI)

- `opencode.jsonc` — project config + permissions (blocks `down -v`, committing models/secrets, etc.).
- `.opencode/agents/` — `rag-designer`, `pgvector-engineer`, `eval-runner`, `reviewer` (specialist subagents).
- `.opencode/commands/` — `/scaffold`, `/eval`, `/db`, `/rules`, `/smoke`.
- `.opencode/skills/` — `rag-dev`, `pgvector-dev` (field checklists for the related domain).

## The promised flow

1. **Automatic ingest**: file → parse → chunk (~512 tokens) → embed (e5-small ONNX, 384d) → Postgres (`halfvec` + tsvector, idempotent via `content_hash`).
2. **Hybrid retrieval**: dense HNSW + sparse GIN → RRF fusion (k=60) → top_k=4 with `source + chunk_index` citations.
3. **Grounded answers**: only from context; without relevant context → a clear refusal (in Malay), no hallucination.
4. **Tested**: faithfulness/recall@k/latency eval with release thresholds (>5% regression blocks merge).
