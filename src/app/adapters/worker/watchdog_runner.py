"""Worker ingest — Watchdog(`data/inbox`) → baris `pending` → poller → IngestUseCase.

Keputusan arsitektur (ADR D5): antrean = tabel `ingest_jobs`; worker IN-PROCESS
(tanpa broker). Jaminan: whitelist ekstensi temp, satu file gagal TIDAK boleh
menghentikan loop (per-file try/except), retry max 2 kali via kolom attempts.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path, PurePosixPath
from typing import Any

import structlog
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.application.ports import IngestJobStore
from app.application.use_cases.ingest import IngestUseCase
from app.domain.documents import IngestJob
from app.domain.errors import IngestError

logger = structlog.get_logger(__name__)

# File sementara ( masih ditulis / temp aplikasi ) skipped, jangan antre.
_TEMP_SUFFIXES: tuple[str, ...] = (".part", ".tmp", ".swp", ".crdownload")
_TEMP_PREFIXES: tuple[str, ...] = ("~$", ".#")


def normalize_inbox_path(raw: str, inbox_root: str) -> str:
    """Normalisasi laluan Windows/absolut → POSIX relatif ke folder inbox.

    `C:\\data\\inbox\\sub\\a.pdf` → `sub/a.pdf`; `/data/inbox/a.pdf` → `a.pdf`.
    Isi path TIDAK berubah selain pemisah & akar — worker membaca file di
    bawah `inbox_root` kontainer.
    """

    posix = str(Path(raw)).replace("\\", "/")
    root = str(Path(inbox_root)).replace("\\", "/").rstrip("/")
    if posix.lower().startswith(root.lower() + "/"):
        posix = posix[len(root) + 1 :]
    else:
        # Laluan Windows disk betul-betul (C:/...) tanpa akar kontainer → buang drive.
        posix = re.sub(r"^[A-Za-z]:/", "", posix)
    parts = PurePosixPath(posix).parts
    return "/".join(p for p in parts if p not in ("", ".", ".."))


def is_temp_file(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(_TEMP_SUFFIXES) or lowered.startswith(_TEMP_PREFIXES)


class _InboxEvent(FileSystemEventHandler):
    """Handler Watchdog: antrekan file baru/berubah ke `IngestJobStore`."""

    def __init__(self, root: str, scheduler: InboxWatchdogRunner) -> None:
        super().__init__()
        self._root = root
        self._scheduler = scheduler

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or getattr(event, "event_type", "") in {"deleted", "closed"}:
            return
        src = getattr(event, "src_path", "") or ""
        if not src:
            return
        rel = normalize_inbox_path(src, self._root)
        if not rel or is_temp_file(Path(rel).name):
            return
        self._scheduler.enqueue(rel)


class InboxWatchdogRunner:
    """Runner in-process: Watchdog thread + poll loop async single worker."""

    def __init__(
        self,
        *,
        ingest: IngestUseCase,
        job_store: IngestJobStore,
        inbox_root: str,
        poll_interval_s: float = 2.0,
        max_attempts: int = 2,
        rescan_interval_s: float = 15.0,
    ) -> None:
        self._ingest = ingest
        self._store = job_store
        self._root = inbox_root
        self._poll_interval = poll_interval_s
        self._max_attempts = max_attempts
        # Sprint 6: rescue-scan berkala (peek-and-poll) mit mtime map —
        # kelewatan watchdog event pada mount Windows→Linux di-toleransi.
        self._rescan_interval = max(rescan_interval_s, 0.0)
        self._loop: asyncio.AbstractEventLoop | None = None
        # watchdog Observer = alias dinamis (tanpa stub stabil) — sesuai override mypy.
        self._observer: Any = None
        self._seen_mtime: dict[str, float] = {}  # rel posix → mtime diproses

    def enqueue(self, rel_path: str) -> None:
        """Dipanggil dari thread Watchdog → coroutine enqueue di loop utama."""

        if self._loop is None:
            return
        coro = self._store.enqueue(rel_path)
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def run(self) -> None:
        """Jalankan sampai di-cancel (dipasang sebagai task lifespan)."""

        self._loop = asyncio.get_running_loop()
        self._observer = Observer()
        Path(self._root).mkdir(parents=True, exist_ok=True)
        self._observer.schedule(_InboxEvent(self._root, self), self._root, recursive=True)
        self._observer.start()
        logger.info("watchdog_started", inbox=self._root)
        try:
            await self._scan_existing()
            await self._poll_loop()
        finally:
            self._stop_observer()

    def _stop_observer(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
            logger.info("watchdog_stopped")

    async def _scan_existing(self) -> None:
        """Saat startup: samakan file yang terlewat saat worker mati (boot-resume).

        Sprint 6: mtime map in-memory dibina di sini — rescans berikutnya hanya
        mengantre fail yang dianggap baru atau berubah.
        """

        root = Path(self._root)
        for path in sorted(root.rglob("*")):
            name = path.name
            if path.is_file() and not is_temp_file(name):
                rel = normalize_inbox_path(str(path), self._root)
                self._remember_mtime(rel, path)
                await self._store.enqueue(rel)

    async def _rescan_existing(self) -> int:
        """Peek-and-poll fallback: hanya fail baru/berubah (sejak mtime) diantrekan.

        Kembalikan bilangan fail yang di-rescue; 0 = tiada berita.
        """

        rescued = 0
        root = Path(self._root)
        try:
            paths = sorted(root.rglob("*"))
        except OSError as exc:
            logger.warning("rescan_failed", error=str(exc))
            return 0
        for path in paths:
            if not path.is_file() or is_temp_file(path.name):
                continue
            rel = normalize_inbox_path(str(path), self._root)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = -1.0
            if self._seen_mtime.get(rel) == mtime:
                continue  # sudah wujud dan tidak berubah
            self._seen_mtime[rel] = mtime
            await self._store.enqueue(rel)
            rescued += 1
        return rescued

    def _remember_mtime(self, rel: str, path: Path) -> None:
        try:
            self._seen_mtime[rel] = path.stat().st_mtime
        except OSError:
            self._seen_mtime[rel] = -1.0

    async def _poll_loop(self) -> None:
        elapsed_since_rescan = 0.0
        rescan_every = self._rescan_interval if self._rescan_interval > 0 else float("inf")
        while True:
            try:
                job = await self._store.claim_pending()
            except Exception as exc:  # SQL Error tidak boleh mematikan worker
                logger.error("poll_failed", error=str(exc))
                await asyncio.sleep(self._poll_interval)
                continue
            if job is None:
                await asyncio.sleep(self._poll_interval)
                elapsed_since_rescan += self._poll_interval
                if elapsed_since_rescan >= rescan_every:
                    elapsed_since_rescan = 0.0
                    rescued = await self._rescan_existing()
                    if rescued:
                        logger.info("rescanned_inbox", rescued=rescued)
                continue
            try:
                # Review Sprint 6: pertahanan terakhir — kegagalan _process
                # yang tersisa tidak boleh membunuh poll loop.
                await self._process(job)
            except Exception as exc:
                logger.error("job_process_unexpected_error", job_id=job.id, error=str(exc)[:500])
            elapsed_since_rescan = 0.0  # aksi fail baru — delay rescan semula

    async def _process(self, job: IngestJob) -> None:
        """Proses satu job — JANGAN pernah membiarkan ralat naik ke poll loop
        (review Sprint 6): blip DB pada mark_* dulu membunuh worker senyap.
        Job pada `running` yang tak terselamat dipulihkan boot-resume."""

        log = logger.bind(job_id=job.id, path=job.path, attempt=job.attempts)
        log.info("job_started")
        try:
            data = await asyncio.to_thread(self._read_bytes, job.path)
            await self._ingest.execute(data, source=job.path)
        except Exception as exc:  # file hilang/pecah/DB — worker tidak boleh mati
            try:
                requeued = await self._store.mark_failed_or_retry(
                    job.id, str(exc), max_attempts=self._max_attempts
                )
            except Exception as mark_exc:  # DB blip — biarkan `running`
                log.error("job_mark_failed_error", mark_error=str(mark_exc), error=str(exc)[:500])
                return
            log.warning("job_failed", error=str(exc)[:500], requeued=requeued)
            return
        try:
            await self._store.mark_done(job.id)
        except Exception as exc:  # DB blip selepas ingest berjaya
            log.error("job_mark_done_error", error=str(exc))
            return
        log.info("job_done")

    def _read_bytes(self, rel_path: str) -> bytes:
        """Baca file dari inbox; error dibungkus domain agar status job teratur."""

        file_path = Path(self._root) / rel_path
        try:
            return file_path.read_bytes()
        except OSError as exc:
            msg = f"file inbox tidak dapat dibaca: {rel_path}"
            raise IngestError(msg) from exc


__all__ = ["InboxWatchdogRunner", "is_temp_file", "normalize_inbox_path"]
