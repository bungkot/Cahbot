"""Konektivitas Postgres (asyncpg) — implementasi infrastruktur.

Aturan (golden rule #4): SQL hidup di lapisan repo ini, selalu parametrised
$1..$n — tidak pernah interpolasi string. FTS language dibaca dari tabel
`_fts_config`, bukan hardcoded (skill: pgvector-dev).
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import structlog

from app.domain.errors import RetrievalError

logger = structlog.get_logger(__name__)

FTS_CONFIG_SQL = "SELECT resolved FROM _fts_config WHERE name = 'fts_config'"


def _embed_literal(values: list[float], dim: int = 384) -> str:
    """Format vektor halfvec aman: '[v1,v2,...]' — elemen diformat, bukan interpolasi input user."""

    return "[" + ",".join(f"{v:.6f}" for v in values[:dim]) + "]"


def _keys_literal(keys: list[tuple[int, int]]) -> str:
    """Kunci (document_id, chunk_index) → definisikan set bertipe untuk ANY()."""

    return "(" + ",".join(f"({d},{c})" for d, c in keys) + ")"


class PgVectorRepository:
    """Implementasi `application.ports.VectorRepository` di atas pgvector."""

    def __init__(self, pool: asyncpg.Pool, fts_config: str, hnsw_ef_search: int = 100) -> None:
        self._pool = pool
        self._fts_config = fts_config
        self._hnsw_ef_search = hnsw_ef_search

    # --- ctors -------------------------------------------------------------

    @classmethod
    async def create_from_env(cls, dsn: str, min_size: int, max_size: int) -> PgVectorRepository:
        """Buat pool + konsol FTS config sekali pada startup."""

        pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        async with pool.acquire() as conn:
            row: str | None = await conn.fetchval(FTS_CONFIG_SQL)
        fts = row or "simple"
        logger.info("db_ready", fts_config=fts, pool_min=min_size, pool_max=max_size)
        return cls(pool, fts)

    # --- VectorRepository --------------------------------------------------

    async def search_dense(self, query_embedding: list[float], top_k: int) -> list[tuple[int, int]]:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(f"SET hnsw.ef_search = {int(self._hnsw_ef_search)}")
                rows: list[asyncpg.Record] = await conn.fetch(
                    """
                    SELECT document_id, chunk_index
                    FROM chunks
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> $1::halfvec
                    LIMIT $2
                    """,
                    _embed_literal(query_embedding),
                    top_k,
                )
        except Exception as exc:
            logger.error("search_dense_failed", error=str(exc))
            msg = "pengambilan vektor gagal"
            raise RetrievalError(msg) from exc
        return [(r["document_id"], r["chunk_index"]) for r in rows]

    async def search_sparse(self, query: str, top_k: int) -> list[tuple[int, int]]:
        try:
            async with self._pool.acquire() as conn:
                # TSQUERY parsing letak fungsinya murni text user — bind variable, aman.
                rows: list[asyncpg.Record] = await conn.fetch(
                    f"""
                    SELECT document_id, chunk_index
                    FROM chunks
                    WHERE tsv @@ plainto_tsquery('{self._fts_config}', $1)
                    ORDER BY ts_rank(tsv, plainto_tsquery('{self._fts_config}', $1)) DESC
                    LIMIT $2
                    """,
                    query,
                    top_k,
                )
        except Exception as exc:
            logger.error("search_sparse_failed", error=str(exc))
            msg = "pengambilan FTS gagal"
            raise RetrievalError(msg) from exc
        return [(r["document_id"], r["chunk_index"]) for r in rows]

    async def fetch_chunks(self, keys: list[tuple[int, int]]) -> list[tuple[int, str, str, int]]:
        if not keys:
            return []
        async with self._pool.acquire() as conn:
            rows: list[asyncpg.Record] = await conn.fetch(
                """
                SELECT d.id AS document_id, d.source, c.content, c.chunk_index
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE (d.id, c.chunk_index) = ANY ($1::record[])
                """,
                _keys_literal(keys),
            )
        return [
            (
                int(r["document_id"]),
                str(r["source"]),
                str(r["content"]),
                int(r["chunk_index"]),
            )
            for r in rows
        ]

    async def upsert_document(
        self,
        source: str,
        content_hash: str,
        chunks: list[Any],
        vectors: list[list[float]],
    ) -> None:
        """Idempoten: hash sama → no-op; berbeda → hapus lama + tulis baru (1 transaksi)."""

        async def _write(conn: asyncpg.Connection) -> None:
            existing: asyncpg.Record | None = await conn.fetchrow(
                """
                SELECT id, content_hash FROM documents WHERE source = $1
                """,
                source,
            )
            if existing is not None and str(existing["content_hash"]) == content_hash:
                return  # idempotent
            doc_id: int = await conn.fetchval(
                """
                INSERT INTO documents (source, content_hash)
                VALUES ($1, $2)
                ON CONFLICT (source) DO UPDATE
                    SET content_hash = EXCLUDED.content_hash, updated_at = now()
                RETURNING id
                """,
                source,
                content_hash,
            )
            if existing is not None:
                await conn.execute("DELETE FROM chunks WHERE document_id = $1", doc_id)
            for chunk, vector in zip(chunks, vectors, strict=True):
                await conn.execute(
                    """
                    INSERT INTO chunks (document_id, chunk_index, content, metadata, embedding)
                    VALUES ($1, $2, $3, $4::jsonb, $5::halfvec)
                    """,
                    doc_id,
                    chunk.chunk_index,
                    chunk.content,
                    "{}",
                    _embed_literal(vector),
                )

        async with self._pool.acquire() as conn, conn.transaction():
            await _write(conn)


def run_blocking(counterpart: Any) -> Any:
    """Helper kecil untuk menjalankan callable sinkron berat di worker thread."""

    return asyncio.to_thread(counterpart)
