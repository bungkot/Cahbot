#!/usr/bin/env bash
# Smoke test (jalankan DI DALAM container dev): /health → POST /ingest → /ingest/jobs.
# Target default = service `dev` yang sedang berjalan (uvicorn di port 8000).
# Override: BASE_URL=http://localhost:8000 bash scripts/smoke.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://dev:8000}"
PDF_PATH="/tmp/smoke-surface.pdf"

echo "[smoke] target: ${BASE_URL}"

# --- 1) /health — tunggu sampai siap (max ~70s) -------------------------------
for i in $(seq 1 35); do
  if curl -fsS "${BASE_URL}/health" > /tmp/smoke-health.json 2>/dev/null; then
    break
  fi
  if [ "$i" = 35 ]; then
    echo "[smoke] GAGAL: /health tidak dijangkau" >&2
    exit 1
  fi
  sleep 2
done
echo -n "[smoke] health: "
cat /tmp/smoke-health.json
echo

# --- 2) PDF kecil buatan (offline) → POST /ingest -----------------------------
uv run python - <<'PY'
import io

import pymupdf

doc = pymupdf.open()
p = doc.new_page()
p.insert_text((72, 72), "# Dasar Cuti")
p.insert_text((72, 100), "Cuti tahunan sebanyak 14 hari bagi setiap staf tetap.")
p.insert_text((72, 128), "Prosedur cuti sakit memerlukan sijil doktor.")
buf = io.BytesIO()
buf.write(doc.tobytes(garbage=3, deflate=True))
with open("/tmp/smoke-surface.pdf", "wb") as f:
    f.write(buf.getvalue())
PY

RESP=$(curl -fsS -X POST "${BASE_URL}/ingest" -F "file=@${PDF_PATH}")
echo "[smoke] ingest response: ${RESP}"

# --- 3) antrean job worker -----------------------------------------------------
echo -n "[smoke] jobs: "
curl -fsS "${BASE_URL}/ingest/jobs?limit=5"
echo

# --- 4) verifikasi --------------------------------------------------------------
uv run python - "$RESP" <<'PY'
import json
import sys

data = json.loads(sys.argv[1])
assert data.get("status") == "done", data
assert int(data.get("n_chunks", 0)) >= 1, data
print(f"[smoke] ingest OK: {data['n_chunks']} chunk(s), hash {data['content_hash'][:12]}…")
PY

echo "[smoke] LULUS"
