# Architecture — rag-mvp

Malay-language RAG chatbot, **offline-first, CPU-only**. Experiment target: an MVP that runs on an 8 GB RAM laptop/VM.

## 1. Architecture decisions (compact ADRs)

| # | Decision | Rejected alternative | Rationale |
|---|---|---|---|
| D1 | Local LLM via `llama-cpp-python` + Qwen3 1.7B GGUF q4_k_m | Cloud APIs (OpenAI/Anthropic) | Data must not leave; zero cost; CPU latency acceptable for an MVP |
| D2 | ONNX embedding `multilingual-e5-small` (384d) | bge-m3/larger models | 384d is enough for Malay, light on CPU, halfvec halves RAM usage 2× |
| D3 | pgvector + Postgres 17 | Qdrant/Milvus/FAISS | Single store (chat + vectors + FTS + job queue), simple ops, ACID for ingest |
| D4 | Hybrid retrieval + RRF | dense-only / sparse-only | Malay mixes in English technical terms; FTS catches exact words, dense catches meaning variation |
| D5 | Redis-like queue dropped; queue = the `ingest_jobs` table | Celery/Redis | The MVP doesn't need a separate broker; simple polling with an in-process worker + watchdog |
| D6 | Chainlit as the UI | custom React | Ready-made chat UI, easy to mount, fits demo & data collection |

**D6-update (Sprint 1, outcome of the Swiss Modernist mockup review):** the decision is **confirmed as Chainlit (mount)** — not a custom FastAPI+template UI. The visual contract (`docs/conventions/ui.md` + `mockup/index.html`) is the style target that Sprint 5 must adopt via Chainlit theming/overrides (custom CSS, mono labels, red accent #E30613); a limitation we accept: pixel-identical parity with the mockup is not guaranteed in the production UI. If Chainlit turns out unable to render footnote citations/ refusal states per the contract, the custom UI candidate reopens as a measured decision (with evidence, not opinion).
| D7 | `halfvec(384)` fp16 embeddings | `vector` fp32 / ivfflat | The HNSW index is far smaller, equivalent recall below 1M chunks |

## 2. Components

```text
┌────────────┐  drop file   ┌───────────────────────────────────────────┐
│ data/inbox │────────────▶ │ app/ingest/ (-watchdog worker)            │
└────────────┘              │  parse (PyMuPDF4LLM/MarkItDown)           │
                            │  chunk (~512 tok, overlap 64)             │
                            │  embed (ONNX e5-small, 384d)              │
                            └──────┬────────────────────────────────────┘
                                   │ upsert (source UNIQUE + content_hash)
                                   ▼
                            ┌───────────────────────────────────────────┐
                            │ Postgres 17 + pgvector                    │
                            │  documents / chunks(halfvec+tsv)          │
                            │  ingest_jobs  / chat_sessions+messages    │
                            └──────┬────────────────────────────────────┘
                                   │ hybrid top_k=4 (RRF k=60)
┌────────────┐    POST /chat  ┌────┴──────────────────────────────────────┐
│  Chainlit  │◀══════════════▶│ app/api/ ─ app/retriever/ ─ app/llm/      │
│  UI        │  streaming     │ FastAPI (uvicorn, 1 worker)  llama.cpp    │
└────────────┘  + citations   │              Qwen3-1.7B-q4_k_m            │
                              └───────────────────────────────────────────┘
```

## 3. Data flow

**Ingest (asynchronous, the hot path is never disturbed):**
1. The watchdog detects new/changed files in `data/inbox/` → creates `ingest_jobs` rows (status `pending`).
2. The polling worker picks up the job → parse → chunk → embed → upsert in one transaction:
   `documents` is upserted on `source UNIQUE`; if `content_hash` is unchanged, the job completes with no rework (idempotent).
3. Failure → status `failed` + error message; a bad file never sinks the worker (per-file try/except).

**Inference (per question):**
1. `POST /chat` → embed the query (prefix `query:`) → in parallel: HNSW search + GIN search.
2. RRF fusion (k=60) → top_k=4 chunks with citation metadata.
3. Build the grounded prompt (Malay) → llama.cpp → streamed tokens.
4. Store the user/assistant pairs in `chat_messages` (+ retrieval metadata in the assistant message column as JSON when available in a later sprint).

## 4. Constraints & limits

- Target total RAM ≈ 4 GB: DB 900 MB, app 3.5 GB (the q4 1.7B model ≈ 1.1 GB + embedding ≈ 130 MB + runtime).
- A single uvicorn process; llama.cpp is not thread-safe per model instance → LLM_THREADS=4 on one instance, requests serialized (a queue of <= 2 MVP users is expected).
- `EMBED_DIM=384` is locked (see `data-model.md`).
- Question → first-token batch target < 3 s on a modern 4-core CPU; throughput ≥ 8 tok/s.

## 5. Execution plan

The full sprint plan (checklists, acceptance, backlog) has a single source of truth in **`ROADMAP.md`** (root) — this document does not duplicate it; update status only there.

Order summary (details & status: see `ROADMAP.md`):

| Sprint | Deliverable |
|---|---|
| 0 — Scaffolding | config, db pool, schemas, repos, `/health`, stub routers, harness + docs ✅ |
| 1 — UI mockup + e2e | Swiss Modernist design system + `mockup/` + Playwright e2e (runs first — UI vision) |
| 2 — Ingest | parser + chunker + worker + idempotency tests |
| 3 — Retrieval | dense+sparse+RRF, EXPLAIN ANALYZE, recall@4 testing |
| 4 — Generation | grounded prompt + streaming + citations + refusal |
| 5 — Production UI + eval | final UI (ADR D6 decision) + eval runner + golden set v1 |
| 6 — Hardening | CI, tuning, v0.1.0 release |
