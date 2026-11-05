"""Use case ingest: parse → chunk → embed → upsert (idempoten via content_hash).

Prinsip (skill: rag-dev — ingest): satu file gagal tidak boleh menggagalkan
worker; idempotensi dijamin oleh `documents.content_hash` di DB. Upsert
dipanggil SEKALI per dokumen (semua chunk) — bukan per batch — supaya cek
hash tidak melewatkan batch berikutnya.
"""

from __future__ import annotations

import structlog

from app.application.ports import Chunker, DocumentParser, Embedder, VectorRepository
from app.domain.documents import IngestResult, ParsedDocument, chunk_metadata
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
        lang: str = "ms",
    ) -> None:
        self._parser = parser
        self._chunker = chunker
        self._embedder = embedder
        self._vector_repo = vector_repo
        self._batch = embed_batch_size
        self._lang = lang

    async def execute(self, data: bytes, source: str) -> IngestResult:
        log = logger.bind(ingest_stage="parse", source=source)
        try:
            document: ParsedDocument = self._parser.parse(data, source)
            chunks = self._chunker.chunk(document)
            if not chunks:
                msg = f"dokumen tidak menghasilkan chunk: {source}"
                raise IngestError(msg)
            log = log.bind(n_chunks=len(chunks), ingest_stage="embed")
            vectors: list[list[float]] = []
            for batch_start in range(0, len(chunks), self._batch):
                batch = chunks[batch_start : batch_start + self._batch]
                vectors.extend([await self._embedder.embed_passage(c.content) for c in batch])
            metadata = chunk_metadata(
                chunks, embed_model=self._embedder.model_name, lang=self._lang
            )
            log = log.bind(ingest_stage="upsert")
            await self._vector_repo.upsert_document(
                source=source,
                content_hash=document.content_hash,
                chunks=chunks,
                vectors=vectors,
                metadata=metadata,
            )
        except IngestError:
            raise
        except Exception as exc:
            log.error("ingest_failed", error=str(exc))
            msg = f"ingest gagal untuk {source}"
            raise IngestError(msg) from exc
        log.info("ingest_done", n_chunks=len(chunks), n_tokens=sum(c.n_tokens for c in chunks))
        return IngestResult(
            source=source,
            content_hash=document.content_hash,
            n_chunks=len(chunks),
            is_new_or_replaced=True,
        )
