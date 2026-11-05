# rag-mvp — Makefile (ALL-IN-CONTAINER, SATU docker-compose.yml)
# Host Windows hanya menjalankan Docker + make; seluruh tooling (lint, test,
# e2e Playwright, eval, psql) dieksekusi di dalam container `dev`.
# Stage dipilih via compose profile — docker-compose.yml tunggal:
#   dev : docker compose --profile dev up -d      (db + dev tooling, kode di-mount)
#   prod: docker compose --profile prod up -d     (db + app image produksi)

DC      = docker compose
PROF    = $(DC) --profile dev
RUN     = $(PROF) run --rm dev      # pemanggilan sekali-jalan (tooling)
EXEC    = $(PROF) exec dev          # ke service `dev` yang sedang berjalan

.PHONY: up up-prod down dev-build logs shell lint typecheck test test-all test-e2e \
        check-all playwright-install smoke eval db-shell db-migrate backup restore mem app

# --- lifecycle ---
up:          # dev stack: db + dev tooling (kode di-mount)
	$(PROF) up -d --build

up-prod:     # production stack: db + app image runtime
	$(DC) --profile prod up -d --build

dev-build:
	$(PROF) build

down:
	# Non-destructive: volumes (pgdata) PRESERVED. Never use `down -v`.
	$(DC) down

logs:
	$(PROF) logs -f dev db

shell:
	$(PROF) exec dev bash

# --- quality (jalan di container) ---
lint:
	$(RUN) uv run ruff check src tests scripts && uv run ruff format --check src tests scripts

typecheck:
	$(RUN) uv run mypy src tests

test:
	$(RUN) uv run pytest -m unit -q

test-all:
	$(RUN) uv run pytest -q

test-e2e:
	$(RUN) uv run pytest -m e2e -q

check-all: lint typecheck test

# --- one-time browser install (di container) ---
playwright-install:
	$(RUN) playwright install --with-deps chromium

# --- smoke / eval ---
smoke:
	$(RUN) bash scripts/smoke.sh

eval:
	$(RUN) uv run python src/eval/run_eval.py

# --- db ---
db-migrate:   # terapkan db/migrations/*.sql berurutan (psql di container db)
	@for f in db/migrations/*.sql; do \
		echo "apply $$f"; \
		cat "$$f" | $(DC) exec -T db psql -U "$${DB_USER:-rag}" -d "$${DB_NAME:-rag}" -v ON_ERROR_STOP=1; \
	done

db-shell:
	$(DC) exec db psql -U "$${DB_USER:-rag}" -d "$${DB_NAME:-rag}"

backup:
	bash scripts/backup.sh

restore:
	bash scripts/restore.sh

mem:
	scripts/mem_probe.sh

# --- app runtime (dev server di dalam container; port 8000 dipetakan) ---
app:
	$(PROF) exec dev uv run uvicorn app.adapters.api:app --host 0.0.0.0 --port 8000
