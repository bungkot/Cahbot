"""State holder konten aplikasi untuk UI Chainlit (Sprint 5 KR-1).

mount = Chainlit di-mount sebagai sub-ASGI dari app FastAPI — ia tidak mewarisi
`app.state`, jadi kontainer (sudah di-warm-up di lifespan) dioperkan lewat
holder proses ini. Komposisi tetap manual: Lifespan = satu-satunya pemasang.
"""

from __future__ import annotations

from app.core.container import Container

_HOLDER: dict[str, Container] = {}


def set_container(container: Container) -> None:
    """Simpan kontainer aktif (dipanggil sekali di lifespan FastAPI)."""

    _HOLDER["container"] = container


def get_container() -> Container | None:
    """Kembalikan kontainer bila app sedang hidup (`None` → UI tahu degraded)."""

    return _HOLDER.get("container")
