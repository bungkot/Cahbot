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
