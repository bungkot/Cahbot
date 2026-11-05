---
description: Change the pgvector schema / indexes / FTS safely
agent: pgvector-engineer
subagent: true
---

Review and change the pgvector schema according to the following request, following the `pgvector-dev` skill rules:
$ARGUMENTS

Mandatory: idempotent migration in `db/migrations/`, a `pytest -m integration` test, `EXPLAIN ANALYZE` for the modified queries. Do not run `docker compose down -v`.
