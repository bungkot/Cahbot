# src/ — source layout

All production code lives here (not at the root), so imports stay clean and the package is explicit.

The structure follows **Clean Architecture** — the dependency rule points inward:

```text
src/
├── app/
│   ├── domain/            (inner)      entities & value objects — NO knowledge of tech
│   ├── application/       (⤶ inward)   use cases + ports (interface/protocol)
│   ├── infrastructure/    (⤶ outward)  asyncpg, ONNX, llama.cpp, parser — implement the ports
│   ├── adapters/          (outward)    FastAPI routes, watchdog worker, Chainlit
│   └── core/              (x-cut)      config, logging, errors, DI container
├── eval/                  eval runner + golden set loader
└── tests  (stays in root/tests)        unit + integration
```

Dependency rules:
- `domain` imports no other layer, at most the stdlib.
- `application` imports only `domain`; it receives dependencies via Protocols (ports), never infrastructure details.
- `infrastructure` & `adapters` may import `application` + `domain` + `core`; they must not import each other.
- All connections/config are consumed via `core.container` (manual composition — no DI framework, simple and mypy-strict type-checkable).
