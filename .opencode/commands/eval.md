---
description: Evaluate RAG quality and report the metrics
agent: eval-runner
subagent: true
---

Run the RAG evaluation: `make eval` (in the container; equivalent to `uv run python src/eval/run_eval.py` — requires the stack running; run `make up` first if needed).
$ARGUMENTS

Report the metrics table (faithfulness, recall@k, MRR, p50/p95 latency) and compare against the baseline in `eval/results/`. If the regression is >5%, show examples of failing questions.
