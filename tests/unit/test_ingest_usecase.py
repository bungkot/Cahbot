"""Unit: IngestUseCase — upsert sekali per dokumen, metadata, pembungkusan error."""

from __future__ import annotations

import pytest

from app.application.ports import Chunker
from app.application.use_cases.ingest import IngestUseCase
from app.domain.documents import Chunk, ParsedBlock, ParsedDocument
from app.domain.errors import IngestError


class StubParser:
    def parse(self, data: bytes, source: str) -> ParsedDocument:
        if data == b"PECHE":
            msg = "parser retak"
            raise ValueError(msg)
        return ParsedDocument(
            source=source, content_hash="hash-" + source, blocks=(ParsedBlock(text="teks"),)
        )


class StubChunker:
    def chunk(self, document: ParsedDocument) -> list[Chunk]:
        return [Chunk(chunk_index=0, content="bahagian satu", heading="H", page=1, n_tokens=2)]


class StubChunkerKosong:
    def chunk(self, document: ParsedDocument) -> list[Chunk]:
        return []


class StubEmbedder:
    def __init__(self) -> None:
        self.passages: list[str] = []

    @property
    def model_name(self) -> str:
        return "stub-model"

    async def warm_up(self) -> None:  # pragma: no cover
        return None

    async def embed_query(self, text: str) -> list[float]:  # pragma: no cover
        return [0.0] * 384

    async def embed_passage(self, text: str) -> list[float]:
        self.passages.append(text)
        return [0.5] * 384


class StubRepo:
    def __init__(self) -> None:
        self.calls = 0
        self.saved_source: str = ""
        self.saved_hash: str = ""
        self.saved_chunks: list[Chunk] = []
        self.saved_vectors: list[list[float]] = []
        self.saved_metadata: list[dict[str, str | int | None]] = []

    async def search_dense(
        self, query_embedding: list[float], top_k: int
    ) -> list[tuple[int, int]]:  # pragma: no cover
        return []

    async def search_sparse(
        self, query: str, top_k: int
    ) -> list[tuple[int, int]]:  # pragma: no cover
        return []

    async def fetch_chunks(
        self, keys: list[tuple[int, int]]
    ) -> list[tuple[int, str, str, int]]:  # pragma: no cover
        return []

    async def upsert_document(
        self,
        source: str,
        content_hash: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        metadata: list[dict[str, str | int | None]] | None = None,
    ) -> None:
        self.calls += 1
        self.saved_source = source
        self.saved_hash = content_hash
        self.saved_chunks = chunks
        self.saved_vectors = vectors
        self.saved_metadata = metadata or []


def build_use_case(
    repo: StubRepo,
    chunker: Chunker | None = None,
) -> tuple[IngestUseCase, StubEmbedder]:
    embedder = StubEmbedder()
    use_case = IngestUseCase(
        parser=StubParser(),
        chunker=chunker or StubChunker(),
        embedder=embedder,
        vector_repo=repo,
        embed_batch_size=2,
    )
    return use_case, embedder


async def test_upsert_sekali_dengan_metadata_lengkap() -> None:
    repo = StubRepo()
    use_case, embedder = build_use_case(repo)
    result = await use_case.execute(b"ok", "fail.txt")

    assert repo.calls == 1  # SATU panggilan — idempotensi hash tidak membelah batch
    assert result.n_chunks == 1
    assert result.content_hash == "hash-fail.txt"
    assert len(repo.saved_chunks) == 1
    assert repo.saved_vectors[0] == [0.5] * 384
    assert embedder.passages == ["bahagian satu"]
    assert repo.saved_metadata[0]["embed_model"] == "stub-model"
    assert repo.saved_metadata[0]["lang"] == "ms"
    assert repo.saved_metadata[0]["heading"] == "H"
    assert repo.saved_metadata[0]["page"] == 1


async def test_parser_error_dibungkus_ingesteror() -> None:
    repo = StubRepo()
    use_case, _ = build_use_case(repo)
    with pytest.raises(IngestError):
        await use_case.execute(b"PECHE", "fail.txt")
    assert repo.calls == 0


async def test_chunk_kosong_ditolak_ingesteror() -> None:
    repo = StubRepo()
    use_case, _ = build_use_case(repo, chunker=StubChunkerKosong())
    with pytest.raises(IngestError):
        await use_case.execute(b"ok", "fail.txt")
    assert repo.calls == 0
