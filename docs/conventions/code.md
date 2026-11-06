# Code conventions — rag-mvp

Python 3.12, mypy strict, ruff (E,F,I,UP,B,SIM,RUF), line 100. All production code lives in `src/` (src layout for the source code — see `src/README.md`).

## Clean Architecture (mandatory)

- Dependency rule: `adapters → application → domain`; `infrastructure` implements `application.ports` (Protocol), never referenced directly by use cases.
- `domain` = pure value dataclasses & dedicated errors; NO imports of FastAPI/asyncpg/llama or anything else.
- Use cases receive capabilities (ports) via constructor; assembly (wiring/composition) happens once in `core/container.py` with manual DI.
- Transport→business translation happens in the adapters (FastAPI route, watchdog, Chainlit); HTTP validation in Pydantic schemas, domain invariants in `domain/`.
- SQL only in `infrastructure/postgres/`, always parametrised.
- The implementations in use are composed in one place; tests inject doubles (port stubs) without stitching in real infrastructure.

## Style

- Small modules with a single responsibility; functions < ~40 lines ideally.
- Async across the whole request path; run heavy synchronous blocks (ONNX/llama.cpp) in `asyncio.to_thread` / an executor — never block the event loop.
- Errors: domain exceptions (`IngestError`, `RetrievalError`) → FastAPI handler → structured HTTP 4xx/5xx; never leak a raw stacktrace to the client.
- Logs: `structlog` JSON; mandatory fields `session_id`, `model`, `latency_ms`; **no** print/standard logging.
- SQL: always fully in the repo layer, parametrised `$1..$n`; SQL comments for non-trivial queries.
- Tests: `unit` marker default; `integration` only when Docker is needed; DB fixtures use a one-off template database.
- Language: code/identifiers in English; comments & docstrings in Bahasa Melayu (project language — golden rule #9).
