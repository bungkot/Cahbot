"""Data layer sifar Chainlit (Sprint 5 KR-1) — persistensi UI DINYAHAKTIF.

Atas peraturan projek (DB hanya via repository kita; clean architecture):
Chainlit 2.x auto-mengaktifkan ChainlitDataLayer bila env `DATABASE_URL`
ada, lalu cuba tulis skema `Thread`/`Step` sendiri ke DB RAG → UndefinedTableError.
Sebaliknya: daftarkan `@cl.data_layer` yang mengembalikan no-op — sejarah
chat kekal dengan `app.infrastructure.postgres.chat` (KR-3 Sprint 4);
UI Chainlit murni paparan (ephemeral steps).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import chainlit as cl

if TYPE_CHECKING:
    from chainlit.types import (
        ElementDict,
        Feedback,
        PaginatedResponse,
        Pagination,
        StepDict,
        ThreadDict,
        ThreadFilter,
    )


class _NullDataLayer:
    """No-op persistence — semua operasi no-op (laman ephemeral).

    Signature digandingkan 1:1 dengan `chainlit.data.base.BaseDataLayer`
    supaya panggilan server tidak sesat. Semua metod diisytiharkan
    eksplisit (mypy-strict).
    """

    async def get_user(self, identifier: str) -> dict[str, object] | None:
        return None

    async def create_user(self, user: object) -> dict[str, object] | None:
        # identiti anonim — tidak didaftarkan (UI tanpa auth, Sprint 6)
        return None

    async def delete_feedback(self, feedback_id: str) -> bool:
        return False

    async def upsert_feedback(self, feedback: Feedback) -> str:
        return ""

    async def create_element(self, element: object) -> None:
        return None

    async def get_element(self, thread_id: str, element_id: str) -> ElementDict | None:
        return None

    async def delete_element(self, element_id: str, thread_id: str | None = None) -> None:
        return None

    async def create_step(self, step_dict: StepDict) -> None:
        return None

    async def update_step(self, step_dict: StepDict) -> None:
        return None

    async def delete_step(self, step_id: str) -> None:
        return None

    async def get_thread_author(self, thread_id: str) -> str:
        return "anonim"

    async def delete_thread(self, thread_id: str) -> None:
        return None

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        from chainlit.types import PageInfo, PaginatedResponse

        # senarai kosong — sidebar Threads memaparkan laman tanpa persistensi
        return PaginatedResponse(pageInfo=PageInfo(False, None, None), data=[])

    async def get_thread(self, thread_id: str) -> ThreadDict | None:
        return None

    async def update_thread(
        self,
        thread_id: str,
        name: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, object] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        return None

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        return None

    async def get_favorite_steps(self, user_id: str) -> list[StepDict]:
        return []


@cl.data_layer
def data_layer() -> Any:
    """Kunci laluan: `config.code.data_layer` menang atas env DATABASE_URL."""

    return _NullDataLayer()
