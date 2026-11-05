"""Embedder ONNX lokal — multilingual-e5-small (384d), tanpa API eksternal.

Syarat e5 (skill rag-dev): prefix WAJIB `query:` / `passage:`. Model dimuat
MALAS (lazy) di thread worker; parsing/pooling tidak menyekat event loop
(docs/conventions/code.md — async penuh pada request path).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from app.core.config import EMBED_DIM  # golden rule #3: 384 terkunci
from app.domain.errors import IngestError

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


class OnnxEmbedder:
    """Implementasi `application.ports.Embedder` — CPUExecutionProvider."""

    def __init__(
        self,
        model_path: str,
        *,
        tokenizer_path: str = "",
        max_seq_tokens: int = 256,
        threads: int = 2,
    ) -> None:
        self._model_path = model_path
        self._tokenizer_path = tokenizer_path
        self._max_seq = max_seq_tokens
        self._threads = threads
        self._session: ort.InferenceSession | None = None
        self._tokenizer: Tokenizer | None = None

    # --- Embedder port ------------------------------------------------------

    @property
    def model_name(self) -> str:
        """Nama model untuk metadata chunk (konvensi docs/data-model.md)."""

        return Path(self._model_path).stem

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed_one(QUERY_PREFIX + text)

    async def embed_passage(self, text: str) -> list[float]:
        return await self._embed_one(PASSAGE_PREFIX + text)

    async def warm_up(self) -> None:
        """Muat model + sahkan dimensi == EMBED_DIM (golden rule #3). Dipanggil lifespan."""

        vectors = await self._run([PASSAGE_PREFIX + "up hangat"])
        assert_embed_dim(vectors)

    # --- intern ------------------------------------------------------------

    async def _embed_one(self, text: str) -> list[float]:
        vectors = await self._run([text])
        return vectors[0]

    async def _run(self, texts: list[str]) -> list[list[float]]:
        """Encode batch kecil → vektor ternormalisasi L2 (thread worker)."""

        return await asyncio.to_thread(self._encode_sync, texts)

    def _loaded(self) -> tuple[ort.InferenceSession, Tokenizer]:
        if self._session is None or self._tokenizer is None:
            self._session, self._tokenizer = self._load()
        return self._session, self._tokenizer

    def _load(self) -> tuple[ort.InferenceSession, Tokenizer]:
        tok_path = self._tokenizer_path or str(Path(self._model_path).with_name("tokenizer.json"))
        if not Path(self._model_path).exists():
            msg = f"model ONNX tidak dijumpai: {self._model_path}"
            raise IngestError(msg)
        if not Path(tok_path).exists():
            msg = f"tokenizer.json tidak dijumpai: {tok_path}"
            raise IngestError(msg)
        tokenizer = Tokenizer.from_file(tok_path)
        tokenizer.enable_truncation(max_length=self._max_seq)
        tokenizer.enable_padding()  # batch panjang seragam → mask digunakan pada pooling
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, self._threads)
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(
            self._model_path, sess_options=options, providers=["CPUExecutionProvider"]
        )
        return session, tokenizer

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        session, tokenizer = self._loaded()
        encodings = tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention,
        }
        input_names = {i.name for i in session.get_inputs()}
        if "token_type_ids" in input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        output = session.run(None, feed)[0]  # (batch, seq, hidden)
        vectors = _mean_pool(output, attention)
        return [[float(x) for x in row] for row in vectors.tolist()]


def _mean_pool(last_hidden: np.ndarray, attention: np.ndarray) -> np.ndarray:
    """Mean pooling dengan mask lalu normalisasi L2 (praktik standard e5)."""

    mask = attention[:, :, None].astype(last_hidden.dtype)
    summed = (last_hidden * mask).sum(axis=1)
    counts = np.clip(mask.sum(axis=1), 1, None)
    vectors = summed / counts
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normalized = vectors / np.clip(norms, 1e-12, None)
    return cast(np.ndarray, normalized)


def assert_embed_dim(vectors: list[list[float]], *, dim: int = EMBED_DIM) -> None:
    """Golden rule #3: dimensi embedding wajib 384 — berhenti segera jika tidak."""

    sample = vectors[0] if vectors else []
    if len(sample) != dim:
        msg = f"dimensi embedding {len(sample)} != EMBED_DIM {dim} (terkunci — migrasi sahaja)"
        raise IngestError(msg)
