"""Unit: ingest worker — normalisasi path, skip file temp, kitaran job & retry."""

from __future__ import annotations

from typing import Any, cast

from watchdog.events import FileSystemEvent

from app.adapters.worker.watchdog_runner import (
    InboxWatchdogRunner,
    _InboxEvent,
    is_temp_file,
    normalize_inbox_path,
)
from app.application.use_cases.ingest import IngestUseCase
from app.domain.documents import (
    Chunk,
    IngestJob,
    IngestJobStatus,
    ParsedBlock,
    ParsedDocument,
)
from app.domain.errors import IngestError

# --- double port (typed) ------------------------------------------------------


class StubParser:
    def parse(self, data: bytes, source: str) -> ParsedDocument:
        if "karat" in source or "hilang" in source:
            msg = f"file tidak dapat diproses: {source}"
            raise IngestError(msg)
        return ParsedDocument(
            source=source, content_hash="h-" + source, blocks=(ParsedBlock(text="teks utuh"),)
        )


class StubChunker:
    def chunk(self, document: ParsedDocument) -> list[Chunk]:
        return [Chunk(chunk_index=0, content="teks utuh", n_tokens=2)]


class StubEmbedder:
    @property
    def model_name(self) -> str:
        return "stub"

    async def warm_up(self) -> None:
        return None

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * 384

    async def embed_passage(self, text: str) -> list[float]:
        return [0.1] * 384


class StubRepo:
    async def search_dense(self, query_embedding: list[float], top_k: int) -> list[tuple[int, int]]:
        return []

    async def search_dense_scored(
        self, query_embedding: list[float], top_k: int
    ) -> list[tuple[int, int, float]]:
        return []

    async def search_sparse(self, query: str, top_k: int) -> list[tuple[int, int]]:
        return []

    async def fetch_chunks(self, keys: list[tuple[int, int]]) -> list[tuple[int, str, str, int]]:
        return []

    async def upsert_document(
        self,
        source: str,
        content_hash: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        metadata: list[dict[str, str | int | None]] | None = None,
    ) -> None:
        self.calls = getattr(self, "calls", 0) + 1


class MemoryJobStore:
    """Stub `IngestJobStore` — semantik requeue SQL direplikasi deterministik."""

    def __init__(self) -> None:
        self._seq = 0
        self._rows: dict[int, dict[str, Any]] = {}
        self.done_ids: list[int] = []

    async def enqueue(self, path: str) -> int:
        self._seq += 1
        self._rows[self._seq] = {"path": path, "status": "pending", "attempts": 0, "error": None}
        return self._seq

    async def claim_pending(self) -> IngestJob | None:
        for job_id, row in self._rows.items():
            if row["status"] == "pending":
                row["status"] = "running"
                return self._as_job(job_id)
        return None

    async def mark_done(self, job_id: int) -> None:
        self._rows[job_id]["status"] = "done"
        self.done_ids.append(job_id)

    async def mark_failed_or_retry(self, job_id: int, error: str, *, max_attempts: int) -> bool:
        row = self._rows[job_id]
        row["attempts"] += 1
        row["error"] = error
        if row["attempts"] < max_attempts:
            row["status"] = "pending"
            return True
        row["status"] = "failed"
        return False

    async def get_job(self, job_id: int) -> IngestJob | None:
        return self._as_job(job_id) if job_id in self._rows else None

    async def list_jobs(self, limit: int = 50) -> list[IngestJob]:
        return [self._as_job(job_id) for job_id in list(self._rows)[:limit]]

    def _as_job(self, job_id: int) -> IngestJob:
        row = self._rows[job_id]
        return IngestJob(
            id=job_id,
            path=str(row["path"]),
            status=IngestJobStatus(str(row["status"])),
            attempts=int(row["attempts"]),
            error=row["error"],
        )


class StubbedRunner(InboxWatchdogRunner):
    """Bypass bacaan diska (data disuntik dari konteks ujian)."""

    def _read_bytes(self, rel_path: str) -> bytes:
        return b"isi fail"


def _ingest_use_case() -> IngestUseCase:
    return IngestUseCase(
        parser=StubParser(), chunker=StubChunker(), embedder=StubEmbedder(), vector_repo=StubRepo()
    )


def _runner() -> tuple[StubbedRunner, MemoryJobStore]:
    store = MemoryJobStore()
    runner = StubbedRunner(
        ingest=_ingest_use_case(),
        job_store=store,  # pyright: ignore[reportArgumentType]
        inbox_root="/data/inbox",
        poll_interval_s=0.01,
        max_attempts=2,
    )
    return runner, store


# --- normalisasi laluan --------------------------------------------------------


def test_windows_absolute_path_ke_rel_posix() -> None:
    assert normalize_inbox_path("C:\\data\\inbox\\laporan.pdf", "/data/inbox") == "laporan.pdf"


def test_windows_nested_herarki() -> None:
    assert (
        normalize_inbox_path("C:\\Users\\h\\data\\inbox\\sub\\a.pdf", "/data/inbox") == "sub/a.pdf"
    )


def test_kontainer_absolute_path() -> None:
    assert normalize_inbox_path("/data/inbox/a.pdf", "/data/inbox") == "a.pdf"


def test_backslash_rel_ke_posix() -> None:
    assert normalize_inbox_path("sub\\c.txt", "/data/inbox") == "sub/c.txt"


def test_sudah_relatif_tetap_utuh() -> None:
    assert normalize_inbox_path("sub/a.md", "/data/inbox") == "sub/a.md"


def test_temp_file_kenalpasti() -> None:
    assert is_temp_file("dokumen.docx.tmp")
    assert is_temp_file("~$surat.docx")
    assert is_temp_file(".#a.pdf")
    assert not is_temp_file("dokumen.docx")


# --- handler watchdog --------------------------------------------------------


class _FakeEvent:
    def __init__(self, event_type: str, src_path: str, is_dir: bool) -> None:
        self.event_type = event_type
        self.src_path = src_path
        self.is_directory = is_dir


class _RecordingRunner(InboxWatchdogRunner):
    """Subclass nyata agar handler _InboxEvent lolos tipe mypy strict."""

    def __init__(self) -> None:
        # Tanpa super().__init__ — unit test tidak butuh dependensi runner.
        self.enqueued: list[str] = []

    def enqueue(self, rel_path: str) -> None:
        self.enqueued.append(rel_path)


def test_handler_antre_file_normal_kecuali_temp_dir_deleted() -> None:
    scheduler = _RecordingRunner()
    handler = _InboxEvent("/data/inbox", scheduler)
    handler.on_any_event(cast(FileSystemEvent, _FakeEvent("created", "/data/inbox/ok.pdf", False)))
    handler.on_any_event(cast(FileSystemEvent, _FakeEvent("created", "/data/inbox/x.tmp", False)))
    handler.on_any_event(cast(FileSystemEvent, _FakeEvent("created", "/data/inbox/dx", True)))
    handler.on_any_event(cast(FileSystemEvent, _FakeEvent("deleted", "/data/inbox/ok.pdf", False)))
    assert scheduler.enqueued == ["ok.pdf"]


# --- kitaran job (runner) ----------------------------------------------------


async def test_berjaya_mark_done() -> None:
    runner, store = _runner()
    await store.enqueue("laporan.pdf")
    job = await store.claim_pending()
    assert job is not None
    await runner._process(job)  # pyright: ignore[reportPrivateUsage]
    assert store.done_ids == [job.id]
    assert store._rows[job.id]["status"] == "done"


async def test_gagal_requeue_hingga_maks_attempts_lalu_failed() -> None:
    runner, store = _runner()
    await store.enqueue("karat.pdf")
    job1 = await store.claim_pending()
    assert job1 is not None
    await runner._process(job1)  # percubaan 1 → requeue
    assert store._rows[job1.id]["status"] == "pending"
    job2 = await store.claim_pending()
    assert job2 is not None and job2.attempts == 1
    await runner._process(job2)  # percubaan 2 → failed final
    assert store._rows[job1.id]["status"] == "failed"
    assert store.done_ids == []


async def test_file_hilang_requeue_dengan_pesan_error() -> None:
    runner, store = _runner()
    await store.enqueue("hilang.pdf")
    job = await store.claim_pending()
    assert job is not None
    await runner._process(job)
    assert store._rows[job.id]["status"] == "pending"
    assert store._rows[job.id]["error"] is not None


async def test_alur_penuh_dokumen_sah() -> None:
    """Smoke: use case sebenar (stub ports) → upsert repo → job `done`."""

    runner, store = _runner()
    await store.enqueue("dokumen sah.txt")
    job = await store.claim_pending()
    assert job is not None
    await runner._process(job)  # pyright: ignore[reportPrivateUsage]
    assert store.done_ids == [job.id]
    assert store._rows[job.id]["status"] == "done"
