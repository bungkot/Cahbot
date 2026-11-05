# ROADMAP — rag-mvp

Source of truth for the execution order to completion. Update this document at the end of every sprint (status: ☐ todo / 🚧 in progress / ✅ done / ⏸ paused + reason).

The running UI code follows the Clean Architecture `src/` layout (`domain → application → infrastructure/adapters`) and the whole OpenCode harness (`/scaffold`, `/eval`, `/db`, `/rules`, `/smoke`) used during the active sprint — see `docs/conventions/code.md`.

General rule for every sprint: green quality gate (`make check-all`) + smoke (if the stack is up) + a review via `/rules` before declaring done. **All commands run inside the dev container** — one `docker-compose.yml` with profiles (`--profile dev` / `--profile prod`; the Windows host only needs Docker + make; rule 6b in `AGENTS.md`).

## New priority (user decision, <date>: start from the UI vision)

Updated order: **UI mockup + Playwright e2e come first** (Sprint 1), followed by the RAG pipeline (Sprints 2–5). Rationale: discussing UI/UX with a real artifact is cheaper than refactoring later; the mockup provides the visual contract for the adapters + the Chainlit/custom UI decision (ADR D6, decided only after the mockup is approved).

---

## Sprint 0 — Architecture foundation ✅ *(done: scaffolding + harness + docs)*
- delivery: `src/` clean architecture layout, domain entities, ports, `ChatUseCase`/`IngestUseCase`, `PgVectorRepository` (parametrised SQL), FastAPI factory (`/health`, `/chat` stub), `core/container.py`.
- small leftovers (moved to the next sprint): `datetime` in logs still lacks an explicit `latency_ms`; the `/ingest`, `/sessions` routers, mount Chainlit. *(the first unit tests (`config`, schemas, RRF fusion) ✅ were done in Sprint 1 — 11 tests passing; the 8 lint/mypy errors were also cleared in Sprint 1)*
- **Acceptance (sprint 0)**: lint/mypy/unit all pass; `/health` 200 in Docker; the `/rules` gate passes.

## Sprint 1 — Swiss Modernist UI mockup + Playwright e2e (target: 2–4 days)
Status: ✅ *(done — v2 mockup revision approved; e2e 5/5 green in the dev container; Lighthouse A11y/BP/SEO 100/100/100; ADR D6-update written in `docs/architecture.md`: stays Chainlit-mount)*
- **KR-1 Design system** — `docs/conventions/ui.md`: Swiss modernist — strict grid, Inter/Helvetica typography, colors: white/carbon + 1 red accent (#E30613), no shadows/rounded corners/ornaments; 8pt modular spacing.
  - [x] written: `docs/conventions/ui.md` (principles, components, a11y)
- **KR-2 Static mockup** — `mockup/index.html` (self-contained): masthead header, sessions sidebar + ingest status, chat thread (flat bubbles with lines, NOT 3D bubbles), answers + numbered footnote citations, refusal state, ingest dropzone.
  - [x] written: `mockup/index.html` + `mockup/README.md` *(v2 revision from user feedback: tagline "Retrieval Augmented Generation", a single `Hari ini` session, empty document list + `Letakkan fail di sini` upload button, empty initial thread, ANDA right-aligned / CAHBOT left-aligned, 8pt source text; e2e updated & still green 5/5)*
- **KR-3 Playwright e2e** — `tests/e2e/test_mockup.py`: load the mockup, send a demo question → answer + 3 citations displayed; refusal state displayed without citations; responsive ≥ 360px; no console errors.
  - [x] mockup HTML/CSS/JS
  - [x] pytest-playwright configuration
  - [x] e2e green — 5 Playwright tests pass in the dev container ("....." 100%): masthead+thread, answer+citations, refusal without citations, composer, responsive 360px. Additional browser QA (real Chrome session): Lighthouse A11y/BP/SEO 100/100/100 after the contrast fix (`#666`) + meta description.
- **KR-4 UI decision** — as an outcome of the mockup: choose Chainlit (mount) vs custom UI (FastAPI + template) — recorded in `docs/architecture.md` § D6-update. *(✅ written: stays Chainlit-mount)*
  - [x] **decision: Chainlit (mount)** — D6-update written in `docs/architecture.md` (ADR D6 confirmed; Chainlit via theming/overrides must approach the mockup's visual contract; re-evaluate in Sprint 5 if it can't).
- **Definition of done**: mockup approved by the user (clear vision), e2e green via `make test-e2e`, ADR D6-update written.
  - ✅ all met: v2 mockup approved ("ok tutup Sprint 1"), e2e 5/5 green, ADR D6-update written; quality gate green (ruff/format/mypy strict + 11 unit + 5 e2e).

## Sprint 2 — Ingest pipeline (target: 3–5 working days)
Status: ✅ done — all KRs green (ruff/format/mypy strict + 11 unit + 7 integration + smoke LULUS: POST /ingest real ONNX e5 → pgvector; `scripts/smoke.sh` created)
- **KR-1 Parser & chunker** — `infrastructure/parsers/`: PDF (PyMuPDF4LLM), docx/xlsx/pptx/md (MarkItDown); `Chunker` token-based ~512 tok, overlap 64, never split headings/tables.
  - [x] `DocumentParser`, `Chunker` interfaces (ports)
  - [x] implement parser per file type + fallback for unknown extensions
  - [x] chunker + boundary unit tests (complex pages, tables, markdown)
- **KR-2 ONNX embedder** — `infrastructure/onnx_embedder.py` (e5-small, `query:`/`passage:` prefixes, batch 32, `asyncio.to_thread`).
  - [x] implement + lazy load + warm-up in the lifespan (+ `models/multilingual-e5-small.onnx` + `tokenizer.json` provisioned on host)
  - [x] dim assertion == 384 (golden rule #3) + tokenizer cache tolerance
- **KR-3 Ingest worker** — `adapters/worker/watchdog_runner.py`: watch `data/inbox/` → `pending` rows in `ingest_jobs` → poll done/failed; max 2 retries; Windows → POSIX path normalisation; a large file must never stop the worker (per-file try/except).
  - [x] Watchdog assembly → `IngestUseCase` (in-process lifespan task, ADR D5; `ingest_jobs.attempts` via migration 002)
  - [x] idempotency tested (same hash → upsert no-op; different hash → replace chunks)
- **KR-4 `/ingest` router** — job status (synchronous POST for the MVP; polling status endpoint), Pydantic response models.
- **KR-5 Tests** — unit (chunker boundaries, domain idempotency) + `integration` (Postgres container, ingest e2e with 1 small PDF).
- **Definition of done**: dropping 3 file types → all indexed in `chunks` with `heading`/`page` metadata; bad files end `failed` without taking the worker down; `make check-all` green.
  - ✅ all met: smoke PDF tersimpan dengan `heading: "Dasar Cuti"` + `page: 1` + model `multilingual-e5-small`; file pecah → IngestError → job `failed` + retry 2× masih membiarkan worker hidup; `make check-all` + integration hijau.

## Sprint 3 — Production-grade retrieval (target: 3–4 days)
Status: ☐ todo
- **KR-1 Hybrid query** — improve the dense/sparse SQL in `PgVectorRepository`: verify with `EXPLAIN (ANALYZE, BUFFERS)` for HNSW + GIN; `hnsw.ef_search=100` per connection.
- **KR-2 RRF & context builder** — already present in `ChatUseCase._fuse_rrf`; add: max context slice ~2,500 tokens; trimming that never cuts the best chunk.
- **KR-3 Quality** — test Recall@4 / MRR@4 with the initial golden set (10 answerable cases + 3 must_refuse).
- **KR-4 Indexing playbook** — run the `pgvector-dev` skill to create the HNSW index after a bulk load (per the init schema).
- **Definition of done**: EXPLAIN shows HNSW+GIN are used; recall@4 ≥ 0.7 on the initial golden set (release threshold 0.80 in Sprint 5).

## Sprint 4 — Grounded generation (target: 4–5 days)
Status: ☐ todo
- **KR-1 LLM gateway** — `infrastructure/llama_cpp_gateway.py`: load the model once at lifespan (n_ctx=4096, n_batch=256, threads=4), `asyncio.to_thread`, streamed tokens.
- **KR-2 Grounded prompt (Malay)** — builder in `application/`: answer ONLY from context; explicit citations `[sumber: fail, bahagian ke-N]`; standard refusal when context is insufficient; temperature 0.2, max_tokens 512.
- **KR-3 History** — store `chat_sessions`/`chat_messages` (new repo, parametrised SQL); load the last N turns; endpoint `/sessions/{id}/messages`.
- **KR-4 Refusal policy** — ChatUseCase: RRF score below threshold → `ChatAnswer.refused()`; the UI displays the refusal message.
- **KR-5 Observability** — per-request logs: `session_id`, `model`, `latency_ms`, tokens/s (structlog), middleware adding `session_id` to all logs.
- **Definition of done**: `/chat` streaming + citations in the response model; answers outside the corpus are 100% refused; clean EXPLAIN & logs.

## Sprint 5 — Production UI (Chainlit OR custom, per the Sprint 1 decision) + passing eval (target: 3–4 days)
Status: ☐ todo
- **KR-1 Chainlit mount** — `adapters/chainlit_app.py` mounted on the app; the UI shows answers + an expandable citation list.
- **KR-2 Golden set v1** — 20+ cases (including 3 must_refuse) in the `docs/eval.md` format.
- **KR-3 Eval runner** — `src/eval/run_eval.py`: faithfulness, recall@4, MRR@4, refusal_precision, p50/p95 latency; save `eval/results/<ts>.json` + a markdown summary.
- **KR-4 Release gate** — a >5% regression blocks merge; add the `/eval` job to the daily workflow.
- **Definition of done**: all metrics ≥ the `docs/eval.md` thresholds; `/smoke` green; end-to-end demo success.

## Sprint 6 — Hardening & polish (target: 3 days)
Status: ☐ todo
- [ ] Simple auth policy (optional API key via env) + per-session rate limit
- [ ] Graceful shutdown (drain the pool, stop jobs) + HTTP timeout/retry tests
- [ ] RAM/latency tuning (`make mem` profile, record result distributions)
- [ ] Document the final runbook update + newly found troubleshooting entries
- [ ] Simple CI (lint + mypy + unit) — GitHub Actions
- [ ] Tag the v0.1.0 release (tag + a short CHANGELOG)

## Backlog (post-release / optional)
- Optional reranker (bge-reranker ONNX CPU) — only if Recall@4 < 0.75 without the reranker → a measured decision, not a default (ADR D4).
- Scanned PDF ingest (light OCR) if the corpus requires it.
- Multi-tenant: multiple collections/namespaces in chunks.
- Export chat history + user feedback (👍/👎) to grow the golden set.
- Streaming UI + embeddings cache for the same question (hash dedup).

---

## Cross-sprint dependencies
UI mockup (1) → Ingest (2) → Retrieval (3) → Generation (4) → production UI + eval (5) → Hardening (6). Sprint 3 can partly run in parallel with 4 (different people) because the port interfaces are already separated by the `Embedder`/`LLMGateway` protocols. The UI mockup (1) does not wait for the pipeline — it only needs a static demo; the production UI (5) consumes the visual contract set by the mockup.

## Oversight principles
1. **Demoable every sprint** — the end-to-end path must run (even narrowly), never isolated modules.
2. `/rules` (review) is mandatory before declaring "done".
3. Eval regressions block merge (documented metrics, not opinions).
4. No scope creep: new ideas → Backlog, not into the running sprint without resetting KRs.
