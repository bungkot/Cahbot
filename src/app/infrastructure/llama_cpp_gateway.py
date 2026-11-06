"""Gateway llama.cpp lokal (Sprint 4 KR-1) — implementasi `application.ports.LLMGateway`.

Model dimuat SEKALI di lifespan (`warm_up`; n_ctx/n_batch/threads dari Settings);
generasi berat (blokir) dijalankan di worker thread (`asyncio.to_thread`) —
event loop tak pernah dihalang. Streaming token delta untuk DoD `/chat` streaming.

Offline-first (golden rule #2): tiada panggilan jaringan; model = fail lokal di
`models/`. Bila fail model tiada, komposisi jatuh ke placeholder (core/container)
— gateway ini tetap memeriksa dan melempar ralat yang jelas.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import structlog

from app.application.grounded_prompt import build_grounded_messages
from app.domain.chat import RetrievedContext
from app.domain.errors import RetrievalError

if TYPE_CHECKING:
    # Modul berat — import tip-masa OK; runtime tidak dimuati di sini.
    from llama_cpp import Llama as LlamaType

logger = structlog.get_logger(__name__)

# StopIteration tidak boleh digandakan lintas thread; gunakan sentinel dalaman.
_STREAM_END: Final = object()

# Tanda blok nalar Qwen3 — token tunggal yang boleh pecah lintas chunk, jadi
# penyaring mesti mengekalkan buffer (lihat _ThinkDeltaFilter).
# Dibina per remitan bagi menghindari pemotoman literal oleh aliran fail.
_THINK_OPEN: Final = "<" + "think>"
_THINK_CLOSE: Final = "<" + "/" + "think>"


def _hold_suffix(buffer: str, *tags: str) -> str:
    """Suffix terpanjang `buffer` yang merupakan prefix awalan salah satu `tags`.

    Untuk menahan detik-akhir buffer yang mungkin permulaan tanda terpisah
    lintas chunk (cth. buffer berakhir selepas `<` / `</` sedangkan tanda
    penuh tiba bersama chunk berikutnya).
    """

    longest = ""
    for tag in tags:
        for size in range(min(len(buffer), len(tag) - 1), 0, -1):
            if buffer.endswith(tag[:size]):
                candidate = buffer[-size:]
                if len(candidate) > len(longest):
                    longest = candidate
                break  # prefix terpanjang bagi tag ini dijumpai
    return longest


class _ThinkDeltaFilter:
    """Penyaring aliran token-delta: buang blok `` hela Qwen3.

    Lapisan ketahanan di gateway: soft-switch `/no_think` sudah ada dalam
    system prompt, tapi model Qwen3 1.7B kadang masih mengeluarkan blok —
    tanpa penapis ini, proses berpikir mentah bocor ke UI. Tanda terpisah
    lintas chunk disimpan sebagai buffer ekor sehingga tanda lengkap.
    """

    def __init__(self) -> None:
        self._in_think: bool = False
        self._tail: str = ""

    def feed(self, delta: str) -> str:
        """Satu delta masuk → teks atas-muka (luar blok pemikiran) keluar."""

        buffer = self._tail + delta
        self._tail = ""
        visible: list[str] = []
        while buffer:
            if self._in_think:
                stop = buffer.find(_THINK_CLOSE)
                if stop < 0:
                    self._tail = _hold_suffix(buffer, _THINK_CLOSE)
                    break
                buffer = buffer[stop + len(_THINK_CLOSE) :]
                self._in_think = False
                continue
            opening = buffer.find(_THINK_OPEN)
            if opening < 0:
                # Tanda tutup yatim (tanpa buka) — buang juga, jangan bocor.
                stray_close = buffer.find(_THINK_CLOSE)
                if stray_close >= 0:
                    visible.append(buffer[:stray_close])
                    buffer = buffer[stray_close + len(_THINK_CLOSE) :]
                    continue
                hold = _hold_suffix(buffer, _THINK_OPEN, _THINK_CLOSE)
                self._tail = hold
                visible.append(buffer[: len(buffer) - len(hold)])
                break
            visible.append(buffer[:opening])
            self._in_think = True
            buffer = buffer[opening + len(_THINK_OPEN) :]
        return "".join(visible)

    def flush(self) -> str:
        """Sisa buffer terakhir di hujung aliran (kosong bila masih di blok)."""

        if self._in_think:
            return ""  # aliran terpotong di tengah pemikiran — buang segala sisa
        tail, self._tail = self._tail, ""
        return tail


class LlamaCppGateway:
    """Model GGUF Qwen3 via llama-cpp-python; urutan-mesej dari `grounded_prompt`."""

    def __init__(
        self,
        model_path: str,
        *,
        n_ctx: int = 4096,
        n_batch: int = 256,
        threads: int = 4,
        max_tokens: int = 512,
        temperature: float = 0.2,
    ) -> None:
        self._model_path = model_path
        self._n_ctx = n_ctx
        self._n_batch = n_batch
        self._threads = threads
        self._max_tokens = max_tokens
        self._temperature = temperature
        # Taip riil via TYPE_CHECKING — import runtime hanya di _load_model
        # (modul berat; mesti hanya dimuati bila GGUF benar-benar dipakai).
        self._model: LlamaType | None = None

    @property
    def model_name(self) -> str:
        return Path(self._model_path).stem

    async def warm_up(self) -> None:
        """Muat model SEKALI (dipanggil sekali di lifespan). Gagal → di-log."""

        # Guard idempoten cukup untuk panggilan lifespan; tanpa konkurensi
        # (asumsi tempat panggilan tunggal — sahib review Sprint 4).
        if self._model is not None:
            return
        if not Path(self._model_path).exists():
            msg = f"model GGUF tidak dijumpai: {self._model_path}"
            logger.error("llm_model_missing", path=self._model_path)
            raise FileNotFoundError(msg)
        start = time.perf_counter()
        self._model = await asyncio.to_thread(self._load_model)
        logger.info(
            "llm_ready",
            path=self._model_path,
            n_ctx=self._n_ctx,
            threads=self._threads,
            load_ms=round((time.perf_counter() - start) * 1000, 1),
        )

    def _load_model(self) -> LlamaType:
        from llama_cpp import Llama  # import berat — hanya saat benar2 dipakai

        return Llama(
            model_path=self._model_path,
            n_ctx=self._n_ctx,
            n_batch=self._n_batch,
            n_threads=self._threads,
            verbose=False,
        )

    # --- port: generation ----------------------------------------------------

    async def generate_grounded(
        self,
        question: str,
        context: RetrievedContext,
        history: tuple[tuple[str, str], ...] = (),
    ) -> str:
        """Jawaban penuh — kumpulan atas aliran token (satu path logika)."""

        chunks: list[str] = []
        async for token in self.stream_grounded(question, context, history):
            chunks.append(token)
        return "".join(chunks)

    def stream_grounded(
        self,
        question: str,
        context: RetrievedContext,
        history: tuple[tuple[str, str], ...] = (),
    ) -> AsyncIterator[str]:
        """Token delta async — setiap `next()` blocking ditarik di worker thread."""

        return self._stream(question, context, history)

    async def _stream(
        self,
        question: str,
        context: RetrievedContext,
        history: tuple[tuple[str, str], ...],
    ) -> AsyncIterator[str]:
        model = self._model
        if model is None:
            logger.error("llm_not_warmed")
            msg = "LLM belum dimuat — panggil warm_up() di lifespan dahulu"
            raise RetrievalError(msg)

        messages = build_grounded_messages(question, context, history)
        gen = await asyncio.to_thread(self._open_stream, model, messages)
        start = time.perf_counter()
        n_tokens = 0
        # Penyiring blok pemikiran Qwen3 (lapisan ketahanan) — buang sebelum
        # teks dilepaskan kepada pemanggil; satu tapisan per aliran.
        think_filter = _ThinkDeltaFilter()
        first_visible = True  # strip depan-whitespace artifact model (cth. awalan "\n\n")
        try:
            while True:
                chunk = await asyncio.to_thread(self._advance, gen)
                if chunk is _STREAM_END:
                    break
                text = self._extract_delta(chunk)
                if text:
                    visible = think_filter.feed(text)
                    if not visible:
                        continue  # nol delta (semua dalam blok pemikiran)
                    if first_visible:
                        visible = visible.lstrip()
                        first_visible = visible != ""
                        if not visible:
                            continue
                    n_tokens += 1
                    yield visible
            tail = think_filter.flush()
            if tail:
                if first_visible:
                    tail = tail.lstrip()
                if tail:
                    yield tail
        finally:
            elapsed_s = max(time.perf_counter() - start, 1e-9)
            logger.info(
                "llm_generated",
                model=Path(self._model_path).name,
                n_tokens=n_tokens,
                tokens_per_s=round(n_tokens / elapsed_s, 1),
                gen_ms=round(elapsed_s * 1000, 1),
            )

    def _extract_delta(self, chunk: object) -> str:
        """Satu chunk stream llama_cpp {'choices':[{'delta':{'content':..}}]} → teks."""

        if not isinstance(chunk, dict):
            return ""
        try:
            delta: object = chunk["choices"][0]["delta"]
        except (KeyError, IndexError, TypeError):
            return ""
        if isinstance(delta, dict):
            content = delta.get("content")
            return content if isinstance(content, str) else ""
        return ""

    @staticmethod
    def _advance(gen: Iterator[object]) -> object:
        """next() di worker thread — StopIteration tak boleh didewaskan silang thread."""

        return next(gen, _STREAM_END)

    def _open_stream(self, model: LlamaType, messages: list[dict[str, str]]) -> Iterator[object]:
        """Buka iterator API llama_cpp (blokir) — dipanggil di worker thread.

        Lib menandai hasil sebagai union stream/non-stream & message types
        TypedDict-nya sendiri; untuk aliran ini hasil direkat ke `Iterator[object]`
        dan pesan dict biasa (role/content) dari `grounded_prompt` diteruskan —
        ekstraksi delta tetap defensif di `_extract_delta`.
        """

        stream: object = model.create_chat_completion(
            messages=messages,  # type: ignore[arg-type]
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            stream=True,
        )
        return cast("Iterator[object]", stream)
