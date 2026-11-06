"""Adapter UI Chainlit (Sprint 5 KR-1) — di-mount sebagai sub-ASGI dari api.py.

Kontrak (docs/conventions/ui.md + D6-update):
- gaya Swiss Modernist via Chainlit theming (public/swiss.css) — paritas piksel
  penuh TIDAK dijamin (keputusan Sprint 1), struktur tetap: jawapan + sitasi
  senarai kaki-bilang; penolakan menampilkan ayat baku tanpa sitasi;
- logika bisnis HANYA menerjemahkan event UI → use case; tiada HTTP diri-panggil
  (container dipakai terus — adapter transport kedua di samping api.py);
- session_id bentuik v4 di-jana per sesi chat, untuk persist sejarah (KR-3
  Sprint 4) yang membaca/menulis dari PostgreSQL lewat use case yang sama;
- lampiran fail (spontaneous upload) → ingest use case — status dilaporkan
  sebagai mesej meta mono (badge keadaan ingest ala mockup).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import chainlit as cl
import structlog

# Side-effect import: modul ini mendaftarkan `@cl.data_layer` null SEBELUM
# sesi pertama — tanpa itu Chainlit jatuh-balik ke ChainlitDataLayer (env
# DATABASE_URL) dan cuba tulis skema `Thread` sendiri ke DB RAG
# (UndefinedTableError). Aturan: DB hanya via repository domain.
import app.adapters.chainlit_data  # noqa: F401 (side-effect, selepas cl)
from app.adapters.chainlit_state import get_container
from app.domain.chat import (
    ChatCitationsEvent,
    ChatFinalEvent,
    ChatTokenEvent,
)

if TYPE_CHECKING:
    from app.core.container import Container
    from app.domain.chat import Citation

logger = structlog.get_logger(__name__)

# monospace meta — shaders ala "technical notes" docs/conventions/ui.md §6
_CITATION_HEADER = "kaki-sitasi (mono):"


def _footnotes(citations: tuple[Citation, ...]) -> str:
    """Sitasi → senarai kaki bernombor: `1. sumber — bahagian ke-N (potongan…)`."""

    if not citations:
        return ""
    lines = "\n".join(
        f"{idx}. {c.source} — bahagian ke-{c.chunk_index + 1} ({c.snippet[:60]}…)"
        for idx, c in enumerate(citations, start=1)
    )
    return f"\n\n---\n{_CITATION_HEADER}\n{lines}"


@cl.on_chat_start
async def _on_chat_start() -> None:
    """Satu sesi browser = satu session_id v4 (kunci sejarah chat KR-3)."""

    cl.user_session.set("session_id", str(uuid.uuid4()))
    container = get_container()
    model = container.llm.model_name if container is not None else "belum siap"
    logger.info("ui_session_start", session_id=cl.user_session.get("session_id"), model=model)


@cl.on_message
async def _on_message(message: cl.Message) -> None:
    """Tanya-jawab berstrim; lampiran → ingest; tiada bisnis di adapter."""

    container = get_container()
    if container is None:
        # degraded (DB/app belum siap) — jujur atas sesi UI
        await cl.Message(content="Maaf, layanan belum siap. Cuba sebentar lagi.").send()
        return

    if message.elements:
        await _handle_ingest(message, container)
        return

    session_id = cl.user_session.get("session_id") or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(session_id=session_id)
    logger.info("ui_chat", session_id=session_id, question_len=len(message.content))

    # DoD: jawapan berstrim + senarai sitasi expandable (kaki-bilang).
    streaming = cl.Message(content="")
    citations: tuple[Citation, ...] = ()
    final: ChatFinalEvent | None = None
    streamed = False
    async for event in container.chat.execute_stream(message.content, session_id):
        if isinstance(event, ChatCitationsEvent):
            citations = event.citations  # tersimpan; senarai dilekat di kaki-jawapan
        elif isinstance(event, ChatTokenEvent):
            streamed = True
            await streaming.stream_token(event.text)
        elif isinstance(event, ChatFinalEvent):
            final = event
    answer = final.answer if final is not None else None
    if answer is None:
        # aliran habis tanpa final (defensive) — sahkan yang belum lengkap
        await streaming.update()
        logger.warning("ui_stream_no_final", session_id=session_id)
        return

    # Laluan tanpa token (penolakan gerbang skor — selesai <1 s): Message()
    # belum pernah di-send, update() tidak memunculkannya di UI → send baru.
    streaming.content = answer.content + _footnotes(citations or answer.citations)
    if streamed:
        await streaming.update()
    else:
        await streaming.send()
    logger.info(
        "ui_chat_done",
        session_id=session_id,
        refused=answer.is_refusal,
        n_citations=len(answer.citations),
    )


async def _handle_ingest(message: cl.Message, container: Container) -> None:
    """Lampiran fail: serahkan ke ingest use case — balas status ala mono badge."""

    for element in message.elements:
        path = getattr(element, "path", None)
        name = getattr(element, "name", None) or "tanpa-nama"
        if not path or not Path(path).exists():
            await cl.Message(content=f"fail tidak dijumpai: {name}").send()
            continue
        source = Path(name).name
        data = Path(path).read_bytes()
        try:
            result = await container.ingest.execute(data, source=source)
            status = f"done — {result.n_chunks} chunk(s), hash {result.content_hash[:12]}…"
        except Exception as exc:  # IngestError → status gagal (docs/conventions/ui.md)
            logger.warning("ui_ingest_failed", source=source, error=str(exc))
            status = f"gagal — {exc}"
        await cl.Message(content=f"ingest[{source}]: {status}").send()
