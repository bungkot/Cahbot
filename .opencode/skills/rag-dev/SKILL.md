---
name: rag-dev
description: Use this skill when changing RAG behaviour — chunking, retrieval, LLM prompt, or eval
---

# RAG development — rag-mvp

Sources of truth: `AGENTS.md`, `docs/architecture.md`, `docs/retrieval.md`, `docs/eval.md`.

## Checklist before touching code

1. Identify the layer being changed: ingest / retrieval / generation / eval. Change one layer per change.
2. Read `docs/architecture.md` § Data flow. Do not bypass inter-layer contracts (Pydantic schemas in `app/schemas.py`).

## Ingest (app/ingest/)

- Parsers: PDF → `pymupdf4llm` (preserve headings/markdown structure); docx/xlsx/pptx/md → `markitdown`.
- Chunking: target size ~512 tokens, overlap ~64; never split mid-heading/table. Store `(source, chunk_index)` — this pair is the citation key.
- Embedding: ONNX `multilingual-e5-small` (with the `query:` / `passage:` prefixes — e5 requires them!), batch per `EMBED_BATCH_SIZE`, output dim 384, stored as `halfvec`.
- Normalise Malay text: strip control characters, keep numbers & units intact.

## Retrieval (app/retriever/)

- Dense: `embedding <=> query_halfvec` HNSW cosine, explicit `hnsw.ef_search`.
- Sparse: `tsv @@ plainto_tsquery('simple'|'indonesian', q)` — read the config from `_fts_config`, never hardcode.
- Fusion: RRF `score = Σ 1/(RRF_K + rank)` with `RRF_K=60`; take `RETRIEVER_TOP_K=4`.
- Reranker: optional hook (interface `rerank(query, chunks) -> ranked`), pass-through by default — do not add heavy dependencies without discussion.
- Always carry complete citation metadata through the pipeline.

## Generation (app/llm/)

- System prompt (Malay): answer ONLY from the given context; cite as `[sumber: fail, bahagian ke-N]`; if the context is insufficient → refuse clearly (golden rule #5).
- llama.cpp: `n_ctx=4096`, `n_batch=256`, max_tokens 512, temperature 0.2. Streaming saves RAM; trim the history to the last N turns.
- `structlog` logs per request: session_id, top_k, latency_ms, tokens/s.

## Eval (src/eval/ — runner; golden data in eval/golden/)

- Add 3–5 new golden cases for every user-visible behaviour improvement.
- `make eval` must pass the thresholds in `docs/eval.md` before merge.

## Anti-patterns

- Calling external APIs (OpenAI/Google) — this project is offline.
- Changing the embedding dimension on the column without a full migration.
- Chunks without source metadata.
- A prompt without a grounded-refusal instruction.
