"""Integrasi: PgChatHistoryRepository (Sprint 4 KR-3) — tabel chat_sessions/messages.

Saat hidup: docker compose --profile dev up -d (db wajib). DB sekali-guna
disediakan conftest.pg_db (tak menyentuh pgdata utama).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from app.infrastructure.postgres.chat import PgChatHistoryRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def repo(pg_db: str) -> AsyncIterator[PgChatHistoryRepository]:
    """Repo ke DB ujian sekali-guna; koneksi ditutup selepas ujian."""

    conn = await asyncpg.connect(dsn=pg_db)
    try:
        yield PgChatHistoryRepository(conn)
    finally:
        await conn.close()


async def test_ensure_session_idempoten(repo: PgChatHistoryRepository) -> None:
    """INSERT ON CONFLICT DO NOTHING — panggilan kedua aman."""

    sid = str(uuid.uuid4())
    await repo.ensure_session(sid)
    await repo.ensure_session(sid)  # kedua kali — aman


async def test_append_dan_recent_messages(repo: PgChatHistoryRepository) -> None:
    """Mesej disimpan urut masa; recent mengembalikan N terakhir urut menaik."""

    sid = str(uuid.uuid4())
    await repo.ensure_session(sid)
    for role, content in [
        ("user", "soal 1"),
        ("assistant", "jawab 1"),
        ("user", "soal 2"),
        ("assistant", "jawab 2"),
        ("user", "soal 3"),
    ]:
        await repo.append_message(sid, role, content)

    recent = await repo.recent_messages(sid, 4)
    assert [(m.role, m.content) for m in recent] == [
        ("assistant", "jawab 1"),
        ("user", "soal 2"),
        ("assistant", "jawab 2"),
        ("user", "soal 3"),
    ]
    assert all(m.created_at is not None for m in recent)


async def test_recent_empty_session(repo: PgChatHistoryRepository) -> None:
    """Sesi tanpa mesej → kosong, bukan error (jalur chat pertama)."""

    sid = str(uuid.uuid4())
    assert await repo.recent_messages(sid, 6) == []
