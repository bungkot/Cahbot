"""Konfigurasi terpusat — satu-satunya sumber (pydantic-settings, baca .env)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Golden rule #3 (AGENTS.md): EMBED_DIM terkunci. Perubahan model embedding
# wajib lewat prosedur migrasi di docs/data-model.md — bukan lewat env.
EMBED_DIM: Literal[384] = 384


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---
    database_url: str = "postgresql://rag:rag@db:5432/rag"
    db_pool_min: int = 1
    db_pool_max: int = 5

    # --- LLM (llama.cpp) ---
    model_path: str = "/models/qwen3-1.7b-q3_k_m.gguf"
    llm_threads: int = 4
    llm_n_ctx: int = 4096
    llm_n_batch: int = 256
    llm_max_tokens: int = 512
    llm_temperature: float = 0.2

    # --- Embeddings ---
    embed_model_path: str = "/models/multilingual-e5-small.onnx"
    embed_tokenizer_path: str = ""  # kosong → tokenizer.json sebelah file model
    embed_batch_size: int = Field(default=32, ge=1)
    embed_max_seq_tokens: int = Field(default=256, ge=32)
    embed_threads: int = Field(default=2, ge=1)

    # --- Ingest worker ---
    ingest_inbox_path: str = "/data/inbox"
    ingest_poll_interval_s: float = Field(default=2.0, gt=0)
    ingest_max_attempts: int = Field(default=2, ge=1)
    # Sprint 6 (Backlog): rescue-scan berkala — watchdog boleh lambat beberapa
    # saat pada mount Windows→Linux; in-memory mtime map hanya mengantre fail
    # baru/berubah. 0 = matikan fallback (watchdog event-only).
    ingest_rescan_interval_s: float = Field(default=15.0, ge=0)

    # --- Retrieval ---
    retriever_top_k: int = Field(default=4, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    # Sprint 3 (KR-1): HNSW_langkah carian per-connection — SET hnsw.ef_search.
    hnsw_ef_search: int = Field(default=100, ge=1)
    # Sprint 3 (KR-2): had konteks yang masuk prompt (~2,500 dari n_ctx 4096).
    context_max_tokens: int = Field(default=2500, ge=128)

    # --- Chat (Sprint 4) ---
    # KR-4: penolakan bila cosine top-1 dense < ambang. Default 0.82 diukur atas
    # golden set (jawab-min 0.8503; tolak max sisa 0.8067 — docs/eval.md);
    # untuk mematikan fitur sekali lulus → -1.0.
    retrieval_refusal_threshold: float = Field(default=0.82, ge=-1.0)
    # KR-3: N mesej terakhir sejarah yang dimuat ke prompt (6 mesej = 3 pasangan
    # soalan-jawaban; N dihitung MESEJ, bukan giliran — review sprint 4).
    chat_history_turns: int = Field(default=6, ge=0)

    # --- Auth & rate limit (Sprint 6 KR-1) ---
    # API key pilihan: kosong → tanpa autentikasi (dev). Diisi → semua route API
    # (kecuali /health) wajib bawa header (default `X-API-Key`) yang tepat.
    api_key: str = ""
    api_key_header: str = "X-API-Key"
    # Had kadar per-sesi (permintaan/menit, sliding window in-process).
    # 0 = matikan had — dibenarkan untuk benchmark & penilaian.
    rate_limit_rpm: int = Field(default=60, ge=0)

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached agar seluruh proses memakai satu instance (server MVP, 1 worker)."""
    return Settings()
