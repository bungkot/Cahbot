---
description: End-to-end smoke test of the local stack
---

Run the full smoke test (everything inside the container, ONE compose + profile):

1. `make up` (docker compose --profile dev up -d --build) and wait for the services to be healthy.
2. `make smoke` (`scripts/smoke.sh` inside the container) — or manually: GET `/health`; POST `/ingest` with one small file from `data/inbox/`; POST `/chat` with a Malay question; verify the answer carries citations (source + chunk_index).
3. `docker compose --profile dev logs dev --tail 50` — check the structlog JSON logs contain session_id/latency_ms.

Report each step: PASS/FAIL + the cause when failing. If a code fix is needed, propose a short diff without editing the files.
