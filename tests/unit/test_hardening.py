"""Unit: hardening Sprint 6 — API key, had kadar, health ping, rantai shutdown.

Semua diuji di lapisan adapters dengan double (tiada Docker, tiada LLM):
- `ApiKeyGuard`: mati/off, header salah/kosong → 401; tepat → lolos.
- `SessionRateLimiter`: window menit-bergeser; jam disuntik (deterministik).
- `create_app` + TestClient: `/health` ping DB (degraded, bukan 500),
  auth 401 bila kunci diaktifkan; rantai shutdown: pool drain dipanggil sekali.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.adapters import api as api_mod
from app.adapters.security import ApiKeyGuard, SessionRateLimiter
from app.core.config import Settings
from app.core.container import Container

pytestmark = pytest.mark.unit


class _FakeRequest:
    """Minimum Request yang dibaca guard: header + path (cast di helper)."""

    _headers: dict[str, str]
    _path: str

    def __init__(self, headers: dict[str, str], path: str = "/chat") -> None:
        self.headers = headers  # starlette Request["headers"] — dipaksa via cast
        self.url = SimpleNamespace(path=path)


def _request(headers: dict[str, str]) -> Request:
    return cast(Request, _FakeRequest(headers))


# --- ApiKeyGuard -------------------------------------------------------------


def _guard(key: str = "rahsia-123") -> ApiKeyGuard:
    return ApiKeyGuard(Settings(api_key=key, rate_limit_rpm=0))


def test_guard_mati_bila_api_key_kosong() -> None:
    guard = ApiKeyGuard(Settings(api_key="", rate_limit_rpm=0))
    assert guard.enabled is False
    guard.check(_request({}))  # no-op — tidak membangkitkan


def test_guard_tepat_header_lolos() -> None:
    _guard().check(_request({"X-API-Key": "rahsia-123"}))


def test_guard_header_kosong_401() -> None:
    with pytest.raises(HTTPException) as ctx:
        _guard().check(_request({}))
    assert ctx.value.status_code == 401


def test_guard_kunci_salah_401() -> None:
    with pytest.raises(HTTPException) as ctx:
        _guard().check(_request({"X-API-Key": "salah"}))
    assert ctx.value.status_code == 401


def test_repr_guard_tidak_membocorkan_kunci() -> None:
    assert "rahsia-123" not in repr(_guard())


# --- SessionRateLimiter ------------------------------------------------------


def _limiter(rpm: int, clock_holder: list[float]) -> SessionRateLimiter:
    def clock() -> float:
        return clock_holder[-1]

    return SessionRateLimiter(_limiter_settings(rpm), clock=clock)


def _limiter_settings(rpm: int) -> Settings:
    return Settings(api_key="", rate_limit_rpm=rpm)


def test_rpm_nol_mematikan_had() -> None:
    limiter = SessionRateLimiter(_limiter_settings(0), clock=lambda: 0.0)
    assert limiter.enabled is False
    limiter.check("s1")  # tak pernah 429 walaupun berulang
    limiter.check("s1")


def test_melebihi_rpm_429() -> None:
    ticks = [0.0]
    limiter = _limiter(3, ticks)
    for _ in range(3):
        limiter.check("s1")
    ticks[0] = 1.0  # masih dalam window
    with pytest.raises(HTTPException) as ctx:
        limiter.check("s1")
    assert ctx.value.status_code == 429


def test_window_bergulir_selepas_60_detik() -> None:
    ticks = [0.0]
    limiter = _limiter(1, ticks)
    limiter.check("s1")
    ticks[0] = 30.0
    with pytest.raises(HTTPException):
        limiter.check("s1")
    ticks[0] = 61.0  # entri pertama sudah luar window → slot lega
    limiter.check("s1")


def test_sesi_lain_tidak_terjejas() -> None:
    limiter = SessionRateLimiter(_limiter_settings(1), clock=lambda: 0.0)
    limiter.check("s1")
    limiter.check("s2")  # sesi lain — dibenarkan


def test_trim_stale_sessions_membebaskan_memori() -> None:
    ticks = [0.0]
    limiter = _limiter(1, ticks)
    limiter.check("s1")
    ticks[0] = 1000.0
    assert limiter.trim_stale_sessions() == 1
    assert limiter.trim_stale_sessions() == 0


# --- Doubles untuk app factory -----------------------------------------------


class _PingingStore:
    async def ping(self) -> bool:
        return True


class _FailingStore:
    async def ping(self) -> bool:
        raise RuntimeError("db turun")


class _EmptyHistory:
    async def recent_messages(self, session_id: str, limit: int) -> list[object]:
        return []


class _WarmDouble:
    @property
    def model_name(self) -> str:
        return "stub"

    async def warm_up(self) -> None:
        return None


def _fake_container(store: object, closer: object = None) -> Container:
    """Container ujian (cast — Composition hanya butuh field yang dialui)."""

    ns = SimpleNamespace(
        chat=None,
        ingest=None,
        ingest_jobs=store,
        embedder=_WarmDouble(),
        llm=_WarmDouble(),
        history=_EmptyHistory(),
        pool_closer=closer,
    )
    return cast(Container, ns)


def _settings(**extra: object) -> Settings:
    base: dict[str, object] = {"api_key": "", "rate_limit_rpm": 0}
    base.update(extra)
    return Settings(**base)  # type: ignore[arg-type]


# --- /health ping DB ---------------------------------------------------------


def test_health_db_ok() -> None:
    app = api_mod.create_app(
        _settings(), container=_fake_container(_PingingStore()), start_worker=False
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["database"] == "ok"


def test_health_db_turun_degraded_bukan_500() -> None:
    app = api_mod.create_app(
        _settings(), container=_fake_container(_FailingStore()), start_worker=False
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/health")
    assert resp.status_code == 200  # /health tidak boleh memuntahkan kekacuan
    assert resp.json()["database"] == "unreachable"


# --- Auth route --------------------------------------------------------------


def test_route_api_api_key_aktif_401_tanpa_header() -> None:
    app = api_mod.create_app(
        _settings(api_key="kunci-rahsia"),
        container=_fake_container(_PingingStore()),
        start_worker=False,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/sessions/00000000-0000-0000-0000-000000000000/messages")
    assert resp.status_code == 401


def test_route_api_api_key_aktif_header_tepat_lolos() -> None:
    app = api_mod.create_app(
        _settings(api_key="kunci-rahsia"),
        container=_fake_container(_PingingStore()),
        start_worker=False,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(
            "/sessions/00000000-0000-0000-0000-000000000000/messages",
            headers={"X-API-Key": "kunci-rahsia"},
        )
    assert resp.status_code == 200


def test_health_tetap_terbuka_bila_api_key_aktif() -> None:
    app = api_mod.create_app(
        _settings(api_key="kunci-rahsia"),
        container=_fake_container(_PingingStore()),
        start_worker=False,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/health")
    assert resp.status_code == 200


# --- Had kadar route --------------------------------------------------------


def test_route_had_kadar_per_sesi_429() -> None:
    app = api_mod.create_app(
        _settings(rate_limit_rpm=1), container=_fake_container(_PingingStore()), start_worker=False
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.get(
            "/sessions/00000000-0000-0000-0000-000000000000/messages",
            headers={"X-API-Key": ""},  # api_key mati
        )
        second = client.get(
            "/sessions/00000000-0000-0000-0000-000000000000/messages",
            headers={"X-API-Key": ""},
        )
    assert first.status_code == 200
    assert second.status_code == 429


# --- Rantai shutdown (Sprint 6 KR-2) -----------------------------------------


async def test_shutdown_drain_pool_terpanggil_sekali() -> None:
    """Lifespan exit: pool_closer dieksekusi tepat sekali (drain 10 s)."""

    calls: list[int] = []

    async def closer() -> None:
        calls.append(1)

    app = api_mod.create_app(
        _settings(), container=_fake_container(_PingingStore(), closer=closer), start_worker=False
    )
    with TestClient(app, raise_server_exceptions=False):
        assert calls == []  # belum shutdown
    assert calls == [1]
