"""Unit: ONNX embedder — pintu model/tokenizer hilang, prefix e5, assertion 384d."""

from __future__ import annotations

import pytest

from app.domain.errors import IngestError
from app.infrastructure.onnx_embedder import (
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    OnnxEmbedder,
    assert_embed_dim,
)


def test_model_name_diambil_dari_stem() -> None:
    embedder = OnnxEmbedder("/models/multilingual-e5-small.onnx")
    assert embedder.model_name == "multilingual-e5-small"


def test_prefix_query_dan_passage_ketat_e5() -> None:
    # Panji rag-dev: e5 wajib prefix; konstanta dipakai seluruh pipeline.
    assert QUERY_PREFIX == "query: "
    assert PASSAGE_PREFIX == "passage: "


async def test_model_tidak_ada_lempar_ingesteror_lazily() -> None:
    embedder = OnnxEmbedder("/models/tidak-ada.onnx")
    with pytest.raises(IngestError):
        await embedder.embed_passage("teks")


async def test_warmup_model_hilang_juga_ingesteror() -> None:
    embedder = OnnxEmbedder("/models/tidak-ada.onnx")
    with pytest.raises(IngestError):
        await embedder.warm_up()


def test_assert_dim_mismatch_gagal_keras() -> None:
    with pytest.raises(IngestError, match="EMBED_DIM"):
        assert_embed_dim([[0.1] * 768])


def test_assert_dim_sah() -> None:
    assert_embed_dim([[0.1] * 384])
