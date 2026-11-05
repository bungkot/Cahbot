"""Logging terstruktur — structlog JSON dengan field wajib (golden rule #10)."""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Pasang sekali di lifespan. Logging standar ikut dirutekan ke structlog."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    # structlog.get_logger() bertipe Any di stubs; ketatkan sekali di sini agar
    # seluruh pemanggil menerima tipe BoundLogger yang eksak.
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
