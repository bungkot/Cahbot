# Eval — rag-mvp

## Golden set (eval/golden/)

JSONL format — one case per line:

```json
{"id": "g001", "question": "Berapa baki cuti tahunan pekerja tetap?", "expected_answer": "12 hari bekerja, +2 hari selepas 5 tahun bekerja.", "source": "handbook-cuti.pdf", "must_refuse": false}
```

Required cases: at least 20 — including 3 `must_refuse: true` cases (queries outside the corpus; refusal behavior is mandatory, no citations).

## Metrics & release thresholds

| Metric | Definition | Release threshold |
|---|---|---|
| faithfulness | the answer is supported by the cited context (deterministic ngram-claim checking + manual sampling) | ≥ 0.90 |
| recall@4 | queries where the correct source page appears in the top 4 | ≥ 0.80 |
| MRR@4 | 1 / rank of the first correct source | ≥ 0.65 |
| refusal_precision | out-of-corpus cases correctly refused | 1.00 (hard) |
| latency | question → first token p50 / p95 | ≤ 3 s / ≤ 8 s |

A >5% relative regression on any metric = merge blocked.

## Running

```bash
make up            # stack must be healthy
make eval          # uv run python src/eval/run_eval.py
```

Summarized results are saved to `eval/results/<timestamp>.json` + a markdown summary for comparing runs.

Runner (Sprint 3) measures retrieval-only metrics against the fused top-4 (RRF — the same fusion used by `ChatUseCase`); LLM-based metrics (faithfulness, e2e latency) land with the Sprint 5 expansion. Seed the corpus first (`make seed` — see runbook) and let the worker ingest it before running `make eval`; when the real corpus replaces the seed, rewrite `eval/golden/golden.jsonl` accordingly.

## Sprint 4 measurement — refusal threshold (KR-4 decisions)

A refusal policy on the absolute **RRF fusion score** was proven **not separable** on the seed corpus: every case (answerable AND must_refuse) tops out at `1/(60+1) = 0.0164`, i.e. the dense and FTS lanes always disagree on rank‑1 and two-lane agreement (score 2/61 ≈ 0.0328) never happens. Root cause of the collapse of the FTS lane: `plainto_tsquery` is AND-semantics — one unmatched lexeme empties the whole lane for natural BM questions.

Decision: the refusal signal is the **top‑1 dense cosine similarity** (`search_dense_scored`, `embedding <=> :halfvec`), measured on the golden set (13 cases):

| Group | top‑1 cosine range |
|---|---|
| answerable (10) | `0.8503 – 0.9209` |
| must_refuse (3) | `0.7859, 0.8067` + one borderline `0.8864` |

- **Active threshold = `RETRIEVAL_REFUSAL_THRESHOLD=0.82`** (mid of the 0.8067–0.8503 gap). Eval result: recall@4 = 1.00, MRR@4 = 1.00, refusal-by-policy 2/3, retrieval latency p50/p95 = 13/25 ms.
- **Borderline case `g011`** ("Berapa baki cuti tahunan saya yang tinggal?") is semantically adjacent to the handbook but asks for personal balance data not in the corpus → it is NOT caught by the score gate and relies on the **prompt-level grounded refusal** (system rule: refuse verbatim when the context does not contain the answer). Verification of that second layer requires the real GGUF (Sprint 4 DoD: `answers outside the corpus are 100% refused` = 2/3 by the measured gate + g011 by prompt; confirm at the first real-LLM smoke).
- `None` cosine (empty dense lane, sparse-only hit) never refuses — an FTS match alone is still answer-supporting context.
- Sentinels/edge notes: `threshold <= -1.0` disables the score gate; when the seed corpus is replaced by the real one, re-measure and update both the threshold and this doc.
