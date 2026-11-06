#!/usr/bin/env bash
# Sprint 6 KR-3 — profil RAM/latensi (di dalam container dev, app hidup).
# Ukur: RSS uvicorn (embedder+GGUF) serta p50/p95 latency /chat (non-stream);
# respons selain 200 dikira berasingan (tidak mengotori taburan latensi).
# Hasil boleh direkam ke docs/runbook.md. Nota: sampel kecil — p95 hampir
# ekor terakhir (bukan keterwakilan statistik penuh); naikkan N bila perlu.
#
# Guna:  docker compose --profile dev exec dev bash scripts/mem_probe.sh [N]

set -euo pipefail

TARGET="${APP_URL:-http://localhost:8000}"
N="${1:-10}"

if [ ! -f /workspace/scripts/mem_probe.sh ]; then
  echo "[mem] skrip dijalankan di dalam container dev sahaja" >&2
  exit 1
fi

# --- RSS proses uvicorn: proses GGUF python — comm-nya `uvicorn` (args ps boleh terpotong)
rss_kb=$(ps -eo comm,rss | awk '$1 ~ /uvicorn/ {sum+=$2} END {print sum+0}')
rss_mb=$((rss_kb / 1024))
echo "[mem] RSS uvicorn: ${rss_mb} MB"

# --- Latency /chat non-stream (N permintaan; soalan dari seed korpus) ---
lat_file=$(mktemp)
fail_file=$(mktemp)
questions=(
  "Berapa hari cuti tahunan?"
  "Apakah syarat tambahan cuti tahunan selepas 5 tahun berkhidmat?"
  "Berapa lama saya perlu serahkan sijil doktor untuk cuti sakit?"
  "Berapa hari cuti bersalin bagi kakitangan wanita?"
  "Cuti tahunan boleh dibawa ke tahun berikutnya?"
)
q_count=${#questions[@]}
for i in $(seq 1 "$N"); do
  q=${questions[$((i % q_count))]}
  body="{\"question\": \"${q//\"/\\\"}\"}"
  status_ms=$(curl -sS -o /dev/null -w '%{http_code} %{time_total}' \
       -X POST "${TARGET}/chat" -H 'Content-Type: application/json' -d "$body")
  status_code=${status_ms%% *}
  ms=$(awk -v t="${status_ms##* }" 'BEGIN {printf "%d", t*1000}')
  if [ "$status_code" = "200" ]; then
    echo "$ms" >> "$lat_file"
  else
    echo "$status_code" >> "$fail_file"
  fi
done

python3 - "$lat_file" "$fail_file" <<'PY'
import sys
lat = sorted(int(x) for x in open(sys.argv[1]) if x.strip())
n_fail = len([l for l in open(sys.argv[2]).read().split() if l])
if lat:
    n = len(lat)
    p50 = lat[n // 2]
    p95 = lat[min(n - 1, max(n // 2, int(n * 0.95) - 1))]
    print(f"[mem] /chat latency ms: p50={p50} p95={p95} n={n} (sampel kecil: p95 hampir ekor)")
else:
    print("[mem] /chat: tiada respons 200")
if n_fail:
    print(f"[mem] /chat FAIL: {n_fail} permintaan bukan-200 (dikecualikan taburan)")
PY

rm -f "$lat_file" "$fail_file"
echo "[mem] tamat — rekam nilai di atas ke docs/runbook.md"
