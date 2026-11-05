"""Unit: konfigurasi terpusat (Settings pydantic-settings, EMBED_DIM terkunci)."""

import pytest

from app.core.config import EMBED_DIM, Settings

pytestmark = pytest.mark.unit


def test_embed_dim_terkunci_384() -> None:
    """Golden rule #3: dimensi embedding tidak boleh berubah lewat env/ad-hoc."""
    assert EMBED_DIM == 384


def test_settings_default_tanpa_env() -> None:
    """Default retrieval/LLM konsisten dengan nilai yang disebut di ROADMAP."""
    s = Settings(_env_file=None)
    assert s.retriever_top_k == 4
    assert s.rrf_k == 60
    assert s.llm_max_tokens == 512
    assert s.llm_temperature == 0.2
    assert s.db_pool_min >= 1
    assert s.db_pool_max >= s.db_pool_min


def test_settings_menerapkan_override_field() -> None:
    """Jalur tunggal konfigurasi: override hanya lewat env/arg — bukan tersebar."""
    s = Settings(_env_file=None, rrf_k=40)
    assert s.rrf_k == 40
    port = Settings(_env_file=None, port=8001)
    assert port.port == 8001
