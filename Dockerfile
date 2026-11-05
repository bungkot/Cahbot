# syntax=docker/dockerfile:1
# Multi-stage, CPU-only, Python 3.12-slim. Models are NEVER baked in.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    LANG=C.UTF-8

WORKDIR /app

# System deps (estimasi: diuji di CI)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        g++ \
        cmake \
        git \
        curl \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv (pinned image channel; fallback to pip if offline)
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

# ---------- Stage: builder ----------
FROM base AS builder

# Copy dependency manifests first for layer caching
COPY pyproject.toml README.md ./
# TODO: ensure hatchling build includes app package; if missing, add explicit COPY

# Install dependencies
# Prefer prebuilt CPU llama-cpp-python wheel where available.
# If you must build from source for portability, set GGML_NATIVE=OFF (default here for max compatibility):
#   CMAKE_ARGS="-DGGML_NATIVE=OFF" pip install llama-cpp-python
# For this stub, use uv pip install with the same flag available via env.
ENV CMAKE_ARGS="-DGGML_NATIVE=OFF"
RUN uv pip install --system --no-cache \
        fastapi uvicorn pydantic-settings structlog \
        langchain-core langchain-text-splitters langchain-community \
        asyncpg watchdog pymupdf4llm markitdown chainlit \
        onnxruntime \
        llama-cpp-python \
    && uv pip uninstall --system ruff mypy pytest 2>/dev/null || true

# ---------- Stage: runtime ----------
FROM base AS runtime

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY src ./src
COPY scripts ./scripts

# Non-root user
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -sf http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
