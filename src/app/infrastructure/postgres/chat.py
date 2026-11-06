"""Repo sejarah percakapan (Sprint 4 KR-3) — implementasi `ChatHistoryRepository`.

SQL hanya di sini, parametrisasi $1..$n dijamin (golden rule #4). Tabel
`chat_sessions` / `chat_messages` dibuat di db/init/001_schema.sql.
"""

from __future__ import annotations

import asyncpg
import structlog

from app.domain.chat import ChatMessage

logger = structlog.get_logger(__name__)


class PgChatHistoryRepository:
    """Simpan sesi & mesej; sejarah terakhir untuk prompt (dibatasi N)."""

    def __init__(self, pool: asyncpg.Pool | asyncpg.Connection) -> None:
        """Pool atau koneksi tunggal — hanya bind `execute`/`fetch` yang dipakai."""

        self._pool = pool

    async def ensure_session(self, session_id: str) -> None:
        """Sisip sesi sekali — uuid dari adapters/domain, bukan input bebas SQL."""

        await self._pool.execute(
            """
            INSERT INTO chat_sessions (id) VALUES ($1::uuid)
            ON CONFLICT (id) DO NOTHING
            """,
            session_id,
        )

    async def append_message(self, session_id: str, role: str, content: str) -> None:
        """Tambah satu mesej ('user' | 'assistant') ke baris sesi."""

        await self._pool.execute(
            """
            INSERT INTO chat_messages (session_id, role, content)
            VALUES ($1::uuid, $2, $3)
            """,
            session_id,
            role,
            content,
        )

    async def recent_messages(self, session_id: str, limit: int) -> list[ChatMessage]:
        """N mesej terakhir mengikut masa menaik (lama dulu) untuk prompt.

        Limit di-injak ≥ 0 di sini — Postgres menolak LIMIT negatif dgn error.
        """

        rows: list[asyncpg.Record] = await self._pool.fetch(
            """
            -- id ikut diproyeksikan dari subquery — outer ORDER BY id butuh kolom itu.
            SELECT role, content, created_at
            FROM (
                SELECT id, role, content, created_at
                FROM chat_messages
                WHERE session_id = $1::uuid
                ORDER BY created_at DESC, id DESC
                LIMIT $2
            ) last_messages
            ORDER BY created_at ASC, id ASC
            """,
            session_id,
            max(0, int(limit)),
        )
        return [
            ChatMessage(
                role=str(r["role"]),
                content=str(r["content"]),
                created_at=r["created_at"].isoformat(),
            )
            for r in rows
        ]
