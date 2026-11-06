"""Konektivitas Postgres (asyncpg) — implementasi infrastruktur.

Aturan (golden rule #4): SQL hidup di lapisan repo ini, selalu parametrised
$1..$n — tidak pernah interpolasi string. FTS language dibaca dari tabel
`_fts_config`, bukan hardcoded (skill: pgvector-dev).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import asyncpg
import structlog

from app.domain.documents import Chunk
from app.domain.errors import RetrievalError

logger = structlog.get_logger(__name__)

FTS_CONFIG_SQL = "SELECT resolved FROM _fts_config WHERE name = 'fts_config'"


def _embed_literal(values: list[float], dim: int = 384) -> str:
    """Format vektor halfvec aman: '[v1,v2,...]' — elemen diformat, bukan interpolasi input user.

    Golden rule #3: dimensi terkunci — panjang tidak sepadan → ralat (bukan
    potongan diam-diam yang menyangkut noda dimensi).
    """

    if len(values) != dim:
        msg = f"dim embedding {len(values)} != {dim} terkunci"
        raise ValueError(msg)
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


class PgVectorRepository:
    """Implementasi `application.ports.VectorRepository` di atas pgvector."""

    def __init__(self, pool: asyncpg.Pool, fts_config: str, hnsw_ef_search: int = 100) -> None:
        self._pool = pool
        self._fts_config = fts_config
        self._hnsw_ef_search = hnsw_ef_search

    # --- ctors -------------------------------------------------------------

    @property
    def pool(self) -> asyncpg.Pool:
        """Pool asyncpg — dipakai repo lain (job store) satu pool yang sama."""

        return self._pool

    @classmethod
    async def create_from_env(
        cls,
        dsn: str,
        min_size: int,
        max_size: int,
        *,
        hnsw_ef_search: int = 100,
    ) -> PgVectorRepository:
        """Buat pool + codec jsonb + konsol FTS config sekali pada startup."""

        async def _init(conn: asyncpg.Connection) -> None:
            # jsonb ↔ dict otomatis untuk SETIAP koneksi pool (baca & tulis).
            await conn.set_type_codec(
                "jsonb",
                encoder=json.dumps,
                decoder=json.loads,
                schema="pg_catalog",
            )
            # HNSW per-connection: SET sekali saat koneksi dibuka (bukan
            # per-ORDER query — tak berinterpolasi user; nilai int).
            await conn.execute(f"SET hnsw.ef_search = {int(hnsw_ef_search)}")

        pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size, init=_init)
        async with pool.acquire() as conn:
            row: str | None = await conn.fetchval(FTS_CONFIG_SQL)
        fts = row or "simple"
        logger.info("db_ready", fts_config=fts, pool_min=min_size, pool_max=max_size)
        return cls(pool, fts, hnsw_ef_search)

    # --- VectorRepository --------------------------------------------------

    async def search_dense(self, query_embedding: list[float], top_k: int) -> list[tuple[int, int]]:
        try:
            async with self._pool.acquire() as conn:
                # hnsw.ef_search sudah di-SET di _init pool — sekali per koneksi.
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
                # FTS config di-bind $1::regconfig — TANPA interpolasi string sama
                # sekali (golden rule #4), baca nilai dari _fts_config pada startup.
                rows: list[asyncpg.Record] = await conn.fetch(
                    """
                    SELECT document_id, chunk_index
                    FROM chunks
                    WHERE tsv @@ plainto_tsquery($1::regconfig, $2)
                    ORDER BY ts_rank(tsv, plainto_tsquery($1::regconfig, $2)) DESC
                    LIMIT $3
                    """,
                    self._fts_config,
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
            # Dua array int paralel (asyncpg bind int[]) — record[] literal string
            # tidak dapat di-bind asyncpg; unnest menjaga semua kunci parametrised.
            doc_ids = [d for d, _ in keys]
            chunk_idxs = [c for _, c in keys]
            rows: list[asyncpg.Record] = await conn.fetch(
                """
                SELECT k.document_id, d.source, c.content, c.chunk_index
                FROM unnest($1::int[], $2::int[]) AS k(document_id, chunk_index)
                JOIN chunks c
                    ON c.document_id = k.document_id AND c.chunk_index = k.chunk_index
                JOIN documents d ON d.id = c.document_id
                """,
                doc_ids,
                chunk_idxs,
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
        chunks: list[Chunk],
        vectors: list[list[float]],
        metadata: list[dict[str, str | int | None]] | None = None,
    ) -> None:
        """Idempoten: hash sama → no-op; berbeda → hapus chunk lama + tulis baru.

        SEMUA chunk ditulis dalam SATU transaksi — cek hash tidak membelah batch.
        """

        if len(chunks) != len(vectors) or len(chunks) != len(metadata or chunks):
            msg = "chunks, vectors, metadata tidak konsisten ukurannya"
            raise ValueError(msg)

        async def _write(conn: asyncpg.Connection) -> None:
            existing: asyncpg.Record | None = await conn.fetchrow(
                "SELECT id, content_hash FROM documents WHERE source = $1",
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
            # dict dibind langsung — codec jsonb di pool meng-encode (jangan dumps manual).
            meta_list = metadata or [{} for _ in chunks]
            rows = [
                (
                    int(doc_id),
                    chunk.chunk_index,
                    chunk.content,
                    dict(meta),
                    _embed_literal(vector),
                )
                for chunk, vector, meta in zip(chunks, vectors, meta_list, strict=True)
            ]
            await conn.executemany(
                """
                INSERT INTO chunks (document_id, chunk_index, content, metadata, embedding)
                VALUES ($1, $2, $3, $4::jsonb, $5::halfvec)
                """,
                rows,
            )

        async with self._pool.acquire() as conn, conn.transaction():
            await _write(conn)


async def run_blocking[T: object](callable_sync: Callable[[], T]) -> T:
    """Helper kecil untuk menjalankan callable sinkron berat di worker thread.

    Generik (PEP 695): tanpa campangan `Any` (golden rule #1); pemanggil
    nama tak berhujah.
    """

    return await asyncio.to_thread(callable_sync)
