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
Status: ✅ done — semua KR hijau: gate (ruff/format/mypy strict + 21 unit + 7 integration + 5 e2e) + smoke LULUS + eval recall@4=1.00/MRR@4=1.00 (sasaran sprint ≥0.70) + EXPLAIN HNSW & GIN terbukti (bukti di docs/runbook.md).
- **KR-1 Hybrid query** — improve the dense/sparse SQL in `PgVectorRepository`: verify with `EXPLAIN (ANALYZE, BUFFERS)` for HNSW + GIN; `hnsw.ef_search=100` per connection.
  - [x] FTS config di-bind `$1::regconfig` (tiada lagi interpolasi f-string — golden rule #4) — disahkan 7 ujian integration atas Postgres dgn config `indonesian` berresolusi dari `_fts_config`
  - [x] `hnsw_ef_search` dari `Settings` → `create_from_env(..., hnsw_ef_search=...)` (masih SET per connection)
  - [x] EXPLAIN (ANALYZE, BUFFERS): HNSW digunakan (tanpa hint pun, 0.12–0.35 ms); GIN digunakan (dgn `enable_seqscan=off` — di 7 baris planner pilih seqscan, semula selepas bulk load sebenar) → bukti penuh di `docs/runbook.md` § evidence
- **KR-2 RRF & context builder** — already present in `ChatUseCase._fuse_rrf`; add: max context slice ~2,500 tokens; trimming that never cuts the best chunk.
  - [x] `app/application/context.py`: `fuse_rrf` (satu-satunya implementasi; `ChatUseCase._fuse_rrf` delegasi), `estimate_tokens` (~4 aksara/token), `build_context(max_tokens, max_chunks)` — chunk terbaik TIDAK PERNAH dipotong, cap `RETRIEVER_TOP_K` dihormati
  - [x] `Settings.CONTEXT_MAX_TOKENS=2500` + `HNSW_EF_SEARCH=100` (+ `.env.example`)
  - [x] unit: `tests/unit/test_context.py` + `tests/unit/test_chat_context.py` (10 kes baharu)
- **KR-3 Quality** — test Recall@4 / MRR@4 with the initial golden set (10 answerable cases + 3 must_refuse).
  - [x] korpus seed BM konstruktif: `scripts/build_seed_corpus.py` (`make seed`) → `handbook-cuti.pdf` + `panduan-klaim-perjalanan.pdf` (fakta konsisten dgn golden set; di-ingest ke DB — watchdog juga disahkan memproses)
  - [x] golden set awal: `eval/golden/golden.jsonl` (10 answerable + 3 must_refuse; format docs/eval.md)
  - [x] runner eval retrieval-only: `src/eval/run_eval.py` — hasil: recall@4=1.00 (10/10), MRR@4=1.00, latency p50/p95=39/122 ms; refusal-tanpa-konteks 0/3 = dijangka (dasar ambang skor RRF bermula Sprint 4); `eval/results/20261106-004153.{json,md}`
- **KR-4 Indexing playbook** — run the `pgvector-dev` skill to create the HNSW index after a bulk load (per the init schema).
  - [x] playbook + langkah EXPLAIN di `docs/runbook.md` § "Indexing playbook (Sprint 3)" + bukti EXPLAIN direkodkan
  - [x] **hardening pasca-review `/rules` (semua dread-betul dihormati tanpa menyentuh skema)**: `SET hnsw.ef_search` ke pool `_init` (sekali per koneksi — p50 eval turun 39→13 ms), `run_blocking` generik PEP-695 (tiada lagi `Any`), guard `_embed_literal` (dim ≠ 384 → ValueError, golden rule #3), `.get` konsisten `_order_chunks`, docstring `build_context` selaras break-on-overflow (+2 uji sempadan), eval: `print()` → structlog + `_source_map` O(n), test tak sentuh attr peribadi; `docs/conventions/code.md` → Bahasa Melayu (selaras codebase); sisa review → Backlog (eval per-kes try/except, dedup wiring, observability `session_id`/`latency_ms` penuh = Sprint 4 KR-5).
- **Definition of done**: EXPLAIN shows HNSW+GIN are used; recall@4 ≥ 0.7 on the initial golden set (release threshold 0.80 in Sprint 5).
  - ✅ met: HNSW+GIN terbukti dalam EXPLAIN; recall@4 = 1.00 ≥ 0.70; smoke LULUS (`docker exec` laluan POST /ingest disahkan; estet: app `degraded` hanya kerana GGUF LLM belum sedia — Sprint 4).
  - Nota backlog (pre-existing, bukan Sprint 3): menjalankan SEMUA suite dalam satu proses `pytest -q` mengadakan konflik pytest-asyncio×pytest-playwright (loop dikongsi) — gate dikekalkan check-all + marker berasingan; port tindakan pink ke Backlog bagi pemiwaian integrasi test-runner; juga: watchdog lewat beberapa saat pada mount Windows (POST /ingest/fallback) → di-Backlog.

## Sprint 4 — Grounded generation (target: 4–5 days)
Status: 🚧 *(semua KR kod selesai & hijau — LLM sebenar telah disahkan (`Qwen3-1.7B-Q3_K_M`; /chat 200 + sitasi + smoke LULUS); baki: kualiti jawapan model — masih mengeluarkan pesan penolakan atas konteks yang cukup, lihat nota DoD)*
- **KR-1 LLM gateway** — `infrastructure/llama_cpp_gateway.py`: load the model once at lifespan (n_ctx=4096, n_batch=256, threads=4), `asyncio.to_thread`, streamed tokens.
  - [x] `LlamaCppGateway` (warm_up sekali-lifespan, streaming token delta via `to_thread`, `model_name` untuk log KR-5); komposisi `core/container`: GGUF wujud → gateway, tiada → `PlaceholderLLMGateway` + warning (app tetap hidup, tanpa halusinasi — golden rule #5). ✅ **verifikasi LLM sebenar (sesi 2026-11-06): `Qwen3-1.7B-Q3_K_M.gguf` (939 MB, SHA-256 disemak) digantikan dari q4_k_m ~1.1 GB atas kekangan RAM host 8 GB + VM WSL Docker Desktop bawaan ~4 GB (crash VM reproducible semasa load; `.wslconfig` dinaik: memory 6 GB, swap 4 GB). Muat 25–27 s, puncak RSS ~1.7 GB; `/chat` 200 dengan sitasi; smoke LULUS; `\n\nMaaf...` lapisan penolakan LLM timbul walau cosine top-1 = 0.9035 — iterasi prompt/eval menyusul.**
  - [x] **penyaring blok pemikiran Qwen3** (pasca-verifikasi): soft-switch `/no_think` di SYSTEM_PROMPT + `_ThinkDeltaFilter` streaming di gateway (tanda terbelah lintas chunk di-buffer; flush hujung aliran; atur tepi: tag tutup yatim dibuang, awalan-whitespace delta pertama dipotong). Kesan: thinking bocor ke `content` (294 unit delta @2.5 tok/s, 117 s → 20 unit @0.9 tok/s, 22 s). 6 ujian unit baharu (`tests/unit/test_think_delta_filter.py`).
- **KR-2 Grounded prompt (Malay)** — builder in `application/`: answer ONLY from context; explicit citations `[sumber: fail, bahagian ke-N]`; standard refusal when context is insufficient; temperature 0.2, max_tokens 512.
  - [x] `application/grounded_prompt.py` — SYSTEM_PROMPT BM (jawab HANYA dari konteks, sitasi `[sumber: fail, bahagian ke-N]` basis-1, pesan penolakan baku verbatim); 6 uji unit. **Kemas kini pasca-LLM-sebenar (2026-11-06, probe Qwen3-1.7B-Q3_K_M): aturan disusun semula — perintah menjawab DAHULU (1-3) → penolakan sebagai pengecualian (4) → contoh format (few-shot, aturan 5) + `/no_think`; pemilihan aturan-penolakan-pertama menghasilkan penolakan buat atas konteks cukup (bukti di `docs/eval.md` § Sprint 4 verification).**
- **KR-3 History** — store `chat_sessions`/`chat_messages` (new repo, parametrised SQL); load the last N turns; endpoint `/sessions/{id}/messages`.
  - [x] `PgChatHistoryRepository` (ensure idempoten / append / recent N urut menaik; SQL parametris penuh) + 3 ujian integration; `ChatUseCase._load_history/_persist` (kegagalan simpan → warning, chat tidak gagal); endpoint GET `/sessions/{id}/messages` (limit ≤50, uuid di-validate → 400); **cengah regresi: `execute()` kini menghabiskan aliran (return-tengah-loop menahan generator → persist tidak berjalan) + ujian**
- **KR-4 Refusal policy** — ChatUseCase: RRF score below threshold → `ChatAnswer.refused()`; the UI displays the refusal message.
  - [x] **(nb. diperbaiki-sesudah-diukur)**: ambang atas *skor RRF* terbukti TIDAK terpisah pada korpus seed (semua kasus 1/61; lane FTS hampir sentiasa kosong — `plainto_tsquery` semantik AND); isyarat diganti **cosine top-1 dense** `search_dense_scored` (port + repo; parametris penuh) → ambang `RETRIEVAL_REFUSAL_THRESHOLD=0.82` dari data golden (JAWAB-min 0.8503; TOLAK-maks-sisa 0.8067): penolakan 2/3 oleh gerbang skor; kasus borderline `g011` (0.8864) dijahit lapisan prompt (grounded refusal) — data & keputusan di `docs/eval.md`
  - [x] aliran event: `ChatCitationsEvent` → `ChatTokenEvent*` → `ChatFinalEvent`; `/chat?stream=true` SSE (`citations → token* → final`) — bingkai JSON bersih (bug dobel-encode telah dicengah)
- **KR-5 Observability** — per-request logs: `session_id`, `model`, `latency_ms`, tokens/s (structlog), middleware adding `session_id` to all logs.
  - [x] `chat_done` (session_id/model/latency_ms/n_chunks/history_turns/refused) + `request_id` middleware contextvars; `llm_generated` tokens/s (gateway); `retrieval_fused` top1_cosine
- **Definition of done**: `/chat` streaming + citations in the response model; answers outside the corpus are 100% refused; clean EXPLAIN & logs.
  - ✅ sebahagian met: gerbang penuh hijau (ruff/format/mypy strict + 41 unit + 11 integration); smoke LULUS (ingest + jobs + purge korpus smoke); API manual: JSON + SSE + refusal + sejarah + 400 bad-uuid; eval (run 071757, pasca-LLM-sebenar) recall@4=1.00/MRR@4=1.00/p50 14 ms, gerbang skor `g012`/`g013` 2/2; `/chat` LLM sebenar (q3_k_m): `g001` ✅ jawap + sitasi format betul, `g012` ✅ tolak. 🚧 **baki (1.7B model-jurang, di-Backlog): `g011` (cosine 0.8864 — di atas ambang 0.82) masih menghalusinasi nombor dasar (12/90) sebagai "baki peribadi" dan bukan penolakan lapisan prompt (W2/W3 pun gagal; thinking 151.7 s tak selesai nalar dalam 512 token) — dokumentasi penuh di `docs/eval.md` § Sprint 4 verification; DoD "100% refused" belum muktamad. Alat sokongan: Makefile `app`/`eval` kini set `PYTHONPATH=/workspace/src` (runner eval sebelumnya gagal ModuleNotFoundError); smoke menambah langkah purge `smoke-surface.pdf` (sekat pencemaran korpus oleh smoke).**

## Sprint 5 — Production UI (Chainlit OR custom, per the Sprint 1 decision) + passing eval (target: 3–4 days)
Status: 🚧 berjalan (KR-1 ✅)
- **KR-1 Chainlit mount** — ✅ ditunaikan: Chainlit 2.12 di-mount sebagai sub-ASGI di `"/"` (`api.py:_mount_chainlit_ui`, mount TERAKHIR; `/api` routes kekal; kegagalan mount → warning + API hidup). Fail: `adapters/chainlit_app.py` (adapter transport: `on_chat_start` jana `session_id` v4 → sejarah KR-3; `on_message` streaming `stream_token` + kaki-bilang sitasi `[sumber: ...]` mono; lampiran → ingest), `adapters/chainlit_state.py` (holder kontainer lifespan), `adapters/chainlit_data.py` (**`@cl.data_layer` nol** — Chainlit 2.12 auto-enable ChainlitDataLayer atas env `DATABASE_URL` dan cuba tulis skema `Thread` ke DB RAG → UndefinedTableError; sejarah kekal di repository domain), `.chainlit/config.toml` (name=Cahbot, cot=hidden, custom_css=/public/swiss.css), `public/swiss.css` (Swiss Modernist: radius 0, aksen #e30613, Inter/IBM Plex Mono). Diversi mypy: `chainlit.*` (follow_imports=skip) + modul adapter (untyped-decorator). Bukti ujian (container dev, app hidup): **e2e Playwright 8/8 LULUS** — `tests/e2e/test_sprint5.py` (3 kes: laman "/" memaparkan composer; jawab "12 hari" + kaki-sitasi mono 4 entri verbatim; penolakan luar korpus baku TANPA sitasi; laluan no-token (refusal) dihantar sebagai `send()` kerana `Message()` yang tak di-stream tidak memuncul dengan `update()` — dijumpai hidup). NB e2e: jangan guna host `dev:8000` (TLD `.dev` = HSTS Chromium → ERR_SSL_PROTOCOL_ERROR; guna `localhost`); tunggu boot websocket (buffer 2 s) sebelum fill — React rerender menghapus input awal. mypy-strict, ruff, 41 unit, smoke (purge korpus), eval recall@4=1.0 — semuanya hijau.
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
- Satukan test-runner: jalankan `pytest -q` SATU proses (unit+integration+e2e) — kini konflik pytest-asyncio × pytest-playwright (loop dikongsi; `RuntimeError: Runner.run() cannot be called from a running event loop`). Gate sebuah sementara: check-all + marker berasingan (pre-existing, bukan Sprint 3).
- Watchdog lewat beberapa saat untuk fail baru pada mount Windows→Linux (peek-and-poll fallback?). POST `/ingest` bekerja sebagai laluan operasi terdekat — selesai di Sprint 6 hardening.
- (review Sprint 3) eval: try/except per kes → `CaseResult` bercatatan supaya satu kes rosak tidak gugurkan agregat penuh; normalisasi nama sumber agar tak senyap-skor 0.
- (Sprint 4) FTS `plainto_tsquery` (semantik AND) membuat lane sparse sering kosong untuk frasa BM semula jadi — pertimbangkan semantik OR / multi-tsquery; kini hybrid efektifnya hanya lane dense untuk soalan biasa.
- (Sprint 4) `search_dense`/`search_dense_scored` hampir pendua (SQL kembar) — cari satu pembina kongsi.
- (Sprint 4) job `running` yang tertinggal bila proses mati tidak dipulihkan saat-startup (requeue stuck-on-boot).
- (review Sprint 3) eval wiring menyalin container composition (model path/pool/hnsw) — pertimbangkan kongsi factory kecil bersama `build_production_container`.
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
