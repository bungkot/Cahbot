"""Use case ingest: parse → chunk → embed → upsert (idempoten via content_hash).

Prinsip (skill: rag-dev — ingest): satu file gagal tidak boleh menggagalkan
worker; idempotensi dijamin oleh `documents.content_hash` di DB.
"""

from __future__ import annotations

import structlog

from app.application.ports import Chunker, DocumentParser, Embedder, VectorRepository
from app.domain.documents import ParsedDocument
from app.domain.errors import IngestError

logger = structlog.get_logger(__name__)


class IngestUseCase:
    """Proses satu dokumen penuh hingga tersimpan. Dipanggil worker/adapters."""

    def __init__(
        self,
        parser: DocumentParser,
        chunker: Chunker,
        embedder: Embedder,
        vector_repo: VectorRepository,
        *,
        embed_batch_size: int = 32,
    ) -> None:
        self._parser = parser
        self._chunker = chunker
        self._embedder = embedder
        self._vector_repo = vector_repo
        self._batch = embed_batch_size

    async def execute(self, data: bytes, source: str) -> None:
        log = logger.bind(ingest_stage="parse", source=source)
        try:
            document: ParsedDocument = self._parser.parse(data, source)
            chunks = self._chunker.chunk(document)
            log = log.bind(n_chunks=len(chunks), ingest_stage="embed")
            for batch_start in range(0, len(chunks), self._batch):
                batch = chunks[batch_start : batch_start + self._batch]
                vectors = [await self._embedder.embed_passage(c.content) for c in batch]
                await self._vector_repo.upsert_document(
                    source=source, content_hash=document.content_hash, chunks=batch, vectors=vectors
                )
        except IngestError:
            raise
        except Exception as exc:
            log.error("ingest_failed", error=str(exc))
            msg = f"ingest gagal untuk {source}"
            raise IngestError(msg) from exc
        log.info("ingest_done")
