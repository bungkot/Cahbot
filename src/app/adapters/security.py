"""Keselamatan transport (Sprint 6 KR-1) — API key pilihan + had kadar per-sesi.

Lapisan adapters (bukan domain): dependency FastAPI murni, tiada logika bisnis.
API key disemak terhadap `Settings.api_key` (env `API_KEY`); kosong → fitur
mati (dev lokal). Had kadar = sliding window menit-bergeser in-process (MVP
satu worker, ADR D5); penskalaan melintang (Redis) kekal di Backlog.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Final

import structlog
from fastapi import HTTPException, Request
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_429_TOO_MANY_REQUESTS

from app.core.config import Settings

logger = structlog.get_logger(__name__)

_WINDOW_S: Final[float] = 60.0
_MAX_SESSIONS: Final[int] = 10_000  # sempad memori MVP (bilangan kunci sesi)


def _constant_time_equal(left: str, right: str) -> bool:
    """Perbandingan masa-malar yang BENAR — `hmac.compare_digest` (stdlib).

    Panjang berbeza juga dilindungi (hmac mengira atas byte-union tanpa
    short-circuit per-bait); jangan diganti pembandingan generator manual.
    """

    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class ApiKeyGuard:
    """Dependency: tahan permintaan tanpa/kunci-API-salah → 401.

    Dipasang pada route API; /health dibiarkan terbuka (load-balancer dan
    websocket UI tidak membawa header).
    """

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.api_key
        self._header = settings.api_key_header
        self.enabled = bool(self._api_key)

    def check(self, request: Request) -> None:
        """Bantah akses — dipanggil dari dependency di api.py."""

        if not self.enabled:
            return
        provided = request.headers.get(self._header, "")
        if not _constant_time_equal(provided, self._api_key):
            logger.warning("auth_rejected", path=request.url.path)
            raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="kunci API tidak sah")

    def __repr__(self) -> str:
        """Tanpa nilai kunci — representasi yang selamat muncul di log."""

        return f"ApiKeyGuard(enabled={self.enabled}, header={self._header})"


class SessionRateLimiter:
    """Had kadar per-klien (sliding window menit-bergeser, in-process).

    Kunci = alamat klien (IP) — `session_id` klien BOLEH dirotar per
    permintaan, jadi tidak boleh menjadi kunci tunggal; dipasangkan sekadar
    kumpulan ip+sid saat tersedia. Melampaui `settings.rate_limit_rpm` → 429;
    0 = matikan. Bilangan kunci di-simpai `_MAX_SESSIONS`: kunci lapuk dibuang
    dahulu, saingnya evakuasi tertua (FIFO) — memori tetap terbatas.
    """

    def __init__(self, settings: Settings, *, clock: Callable[[], float] | None = None) -> None:
        self._rpm = settings.rate_limit_rpm
        self.enabled = self._rpm > 0
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._clock = clock or time.monotonic

    def check(self, session_key: str) -> None:
        """Tahan 429 bila kunci sudah di atas had dalam window aktif."""

        if not self.enabled or not session_key:
            return
        now = self._clock()
        self._bound_size()
        bucket = self._hits[session_key]
        while bucket and now - bucket[0] > _WINDOW_S:
            bucket.popleft()
        if len(bucket) >= self._rpm:
            logger.warning("rate_limited", session_key=session_key, rpm=self._rpm)
            raise HTTPException(status_code=HTTP_429_TOO_MANY_REQUESTS, detail="had kadar sesi")
        bucket.append(now)

    def _bound_size(self) -> None:
        """Sempad bilangan kunci: buang kunci lapuk dulu; masih terhad →
        kunci tertua (FIFO) dikorbankan itu. Memori dikawal, bukan tak berhad."""

        if len(self._hits) < _MAX_SESSIONS:
            return
        self.trim_stale_sessions()
        overflow = len(self._hits) - _MAX_SESSIONS // 2
        for key in list(self._hits.keys())[:overflow]:
            self._hits.pop(key, None)

    def trim_stale_sessions(self) -> int:
        """Pembersihan berkala (task lifespan): buang kunci yang window kosong.

        Dikembalikan: bilangan kunci yang dibuang.
        """

        now = self._clock()
        stale = [
            key for key, bucket in self._hits.items() if not bucket or now - bucket[0] > _WINDOW_S
        ]
        for key in stale[:_MAX_SESSIONS]:
            self._hits.pop(key, None)
        return len(stale)
