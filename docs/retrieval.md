# Retrieval design — rag-mvp

## Pipeline

```text
query ──embed(prefix "query:")──┐
                                ├─► dense: HNSW halfvec cosine (ef_search=100)
                                ├─► sparse: GIN tsvector @@ plainto_tsquery(_fts_config)
                                └─► RRF fusion (k=60) ► top_k=4 (+ citation metadata) ► prompt
```

## RRF formula

`score(d) = Σ_lanes 1 / (60 + rank_lane(d))` — two lanes, ranks start at 1. Stable, no tuning of differently-scaled scores.

## Parameters (all from env, rendered in `.env.example`)

| Param | Default | Notes |
|---|---|---|
| `RETRIEVER_TOP_K` | 4 | chunks that enter the prompt |
| `RRF_K` | 60 | RRF smoothing constant |
| `hnsw.ef_search` | 100 | set per connection in `app/db.py` (not globally in the DB) |

## Policy conflicts

- Maximum prompt context ~2,500 tokens (the rest goes to question + history + answer within n_ctx 4096). If top_k=4 exceeds it, trim the weakest chunks — never degrade the quality of the best chunk.
- A query with no hits (low maximum RRF score) → **fallback**: return the structured `refusal` (sprint 3) — the fixed Malay message per golden rule #5 — the UI renders it with no citations. No hallucination.
- Citations are mandatory: `source` (file name), `chunk_index`, snippet. Implementation: `ChatResponse.citations: list[Citation]`.
