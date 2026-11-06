# CHANGELOG — rag-mvp

Format: [Keep a Changelog](https://keepachangelog.com/) (ringkas MVP); versi
mengikut [SemVer](https://semver.org/). Projek: chatbot RAG Bahasa Melayu
(offline, llama.cpp + pgvector; lihat `docs/architecture.md`).

## [Unreleased]

### Dibetulkan

- **Halusinasi soalan keadaan-peribadi (kes `g011`)** — pasca-rilis Sprint 6:
  guard penafian deterministik baharu `app.application.refusal_guard` menolak
  soalan "baki/milik sendiri" (mis. "Berapa baki cuti tahunan saya yang
  tinggal?") SEBELUM embedding/LLM (`PERSONAL_STATE_NOT_IN_CORPUS`) — data
  dinamik per-orang tidak pernah ada dalam korpus dasar statik. Lapisan prompt
  gagal menawannya pada model 1.7B (bukti docs/eval.md § Sprint 4); kini
  dijawab dengan penolakan baku segera (~ms, tanpa kos LLM). Golden set
  diperbesar 20 → 24 kes (g021–g023 `must_refuse` + g024 kontrol boleh-jawab);
  `KNOWN_GAP_IDS` dikosongkan — refusal_precision gerbang kini 6/6 = 1.00
  tanpa pengecualian (hasil `eval/results/20261106-130134.json`).
- **Over-refusal pada kalimat negatif (kes `g014`/`g024`) — lanjutan hari
  sama**: (1) frasa korpus seed dibetulkan positif-dahulu ("baki cuti boleh
  diwarisi … sehingga 90 hari") + re-ingest; (2) aturan 6 baru di prompt
  grounded (refleks berkecuaian — jangan menolak kerana perkataan soalan ≠
  perkataan konteks). Probe 3 varian × 4 soalan atas LLM sebenar; g014/g024
  kini dijawab tepat + sitasi; kawalan balik (g001 betul, g011/g012/g013
  masih ditolak pada lapisan betul); gerbang rilis `passed=True`, e2e 8/8,
  smoke LULUS (bukti docs/eval.md § pasca-Sprint 6).

## [0.1.0] — 2026-11-06

Rilis MVP pertama — semua sprint (0–6) selesai. Tag: `v0.1.0`.

### Ditambah

- **Pipeline ingest** (Sprint 2): parser PDF (PyMuPDF4LLM) / Office (MarkItDown)
  → chunker token (~512, overlap 64, tidak membelah heading/jadual) → embedder
  ONNX `multilingual-e5-small` (384d, terkunci) → pgvector upsert idempoten;
  worker Watchdog in-process (ADR D5) dengan retry ≤ 2, whitelist fail temp
  (`.tmp`, `~$`, …) dan rescue-scan berkala (health-check). Endpoint
  `POST /ingest` (sync), `GET /jobs`, `GET /jobs/{id}`.
- **Retrieval produksi** (Sprint 3): hybrid HNSW + GIN/FTS lewat
  `PgVectorRepository` (SQL parametris penuh, FTS config di-resolve dari
  tabel `_fts_config`), fusion RRF + pembina konteks (cap ~2,500 token; chunk
  terbaik tidak pernah dipotong); bukti `EXPLAIN (ANALYZE)` di runbook.
- **Grounded generation** (Sprint 4): gateway llama.cpp (Qwen3-1.7B GGUF,
  n_ctx 4096, `asyncio.to_thread`, streaming token); prompt sistem Bahasa
  Melayu dengan sitasi `[sumber: fail, bahagian ke-N]` + pesanan penolakan
  baku; penapis blok pemikiran Qwen3 (`/no_think` + filter delta streaming);
  polisi penolakan berpandukan ambang cosine top-1 (0.82, dari golden set);
  sejarah `chat_sessions`/`chat_messages` (`GET /sessions/{id}/messages`).
  Observability structlog: `session_id`, `model`, `latency_ms`, tokens/s.
- **UI Chainlit** (Sprint 5): di-mount sebagai sub-ASGI di `/` (adaptasi tema
  Swiss Modernist `public/swiss.css`); kaki-sitasi bernombor; penolakan dengan
  strip merah (blockquote → CSS); upload fail → ingest. Laman mockup statik
  `mockup/index.html` + ujian e2e Playwright sebagai kontrak visual.
- **Eval & gerbang rilis** (Sprint 5): golden set 20 kes (`eval/golden/`),
  `src/eval/run_eval.py` — recall@4, MRR@4, refusal_precision (keras 1.00),
  p50/p95 latency; `passed=False` → `SystemExit(1)`. Hasil semasa: recall@4
  1.00, MRR@4 1.00.
- **Hardening** (Sprint 6): API key pilihan (`API_KEY`, dibandingkan dengan
  timing-safe) + had kadar per-klien IP (`RATE_LIMIT_RPM`, 429); graceful
  shutdown (worker dibatalkan, pool asyncpg di-drain dengan had masa 10 s);
  boot-resume job `running` tertinggal; ping DB pada `/health` (degraded,
  bukan 500 saat DB turun); rescue-scan watchdog untuk mount perlahan; CORS
  Chainlit ditaatkan; CI GitHub Actions (lint + mypy strict + unit).

### Keterbatasan MVP (Sprint 6, yang diketahui)

- `g011` borderline: konteks cukup (cosine 0.8864) tetapi model 1.7B mungkin
  menghalusinasi nombor dasar — dikecualikan daripada gerbang rilis
  (`KNOWN_GAP_IDS`), didokumenkan `docs/eval.md`.
- Penolakan berasaskan skor boleh tumpang lalu pada frasa penuh yang jarang.
- Had kadar/API key berasaskan proses-tunggal (Redis/oidca) → Backlog.

### Menjalan (Ringkas)

Lihat `README.md` / `docs/runbook.md`. Ringkas:

```
docker compose --profile dev up -d --build
make app      # uvicorn (di dev container)
make seed     # korpus seed BM (pilihan)
curl http://localhost:8000/health
```

[0.1.0]: https://github.com/rag-mvp/releases/tag/v0.1.0
