#!/usr/bin/env bash
# Unduh model otomatis dari Hugging Face — untuk CI GitHub Actions (tanpa Docker
# dev container) dan host Linux. Offline-first (golden rule #2): model adalah
# FAIL LOKAL; unduhan ini persiapan, bukan panggilan runtime. models/ tidak
# pernah di-commit.
#
# Pemakaian:
#   bash scripts/download_models.sh [all|llm|e5]     (default: all)
# Env (bisa dioverride tanpa menyentuh skrip):
#   MODELS_DIR  default "models" (relatif ke repo root)
#   LLM_URL     default GGUF Qwen3-4B-Instruct-2507 Q4_K_M (~2.6 GB)
#   E5_BASE     default onnx-community/multilingual-e5-small-ONNX
#
# Cache di Actions: job harus cache $MODELS_DIR via actions/cache; skrip ini
# SKIP unduhan bila file target sudah ada (cache hit = lebih cepat).
set -euo pipefail

MODE="${1:-all}"
MODELS_DIR="${MODELS_DIR:-models}"
LLM_FILE="Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
LLM_URL="${LLM_URL:-https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507-GGUF/resolve/main/${LLM_FILE}}"
E5_BASE="${E5_BASE:-https://huggingface.co/onnx-community/multilingual-e5-small-ONNX/resolve/main}"

mkdir -p "${MODELS_DIR}/e5/onnx"

fetch() { # fetch <url> <dest-file> — idempoten: skip bila file sudah ada
    local url="$1"
    local dest="$2"
    if [ -s "${dest}" ]; then
        echo "[models] sudah ada, di-skip: ${dest}"
        return 0
    fi
    echo "[models] unduh: ${url} → ${dest}"
    # Bila URL 404 (file/repo pindah), gagal keras di sini — jangan diembunkan.
    curl -fL --retry 3 --retry-delay 2 -o "${dest}.part" "${url}"
    mv "${dest}.part" "${dest}"
}

case "${MODE}" in
    llm)
        fetch "${LLM_URL}" "${MODELS_DIR}/${LLM_FILE}"
        ;;
    e5)
        # FP32 (onnx/model.onnx) — IDENTIK dengan model di dev host (~450 MB):
        # ambang kosinus penolakan (0.82) diukur atas e5 fp32; varian
        # quantized akan menggeser skor dan mencemari gerbang eval.
        fetch "${E5_BASE}/onnx/model.onnx" "${MODELS_DIR}/e5/onnx/model.onnx"
        fetch "${E5_BASE}/tokenizer.json" "${MODELS_DIR}/e5/tokenizer.json"
        ;;
    all)
        fetch "${LLM_URL}" "${MODELS_DIR}/${LLM_FILE}"
        fetch "${E5_BASE}/onnx/model.onnx" "${MODELS_DIR}/e5/onnx/model.onnx"
        fetch "${E5_BASE}/tokenizer.json" "${MODELS_DIR}/e5/tokenizer.json"
        ;;
    *)
        echo "[models] mode tidak dikenal: ${MODE} (gunakan all|llm|e5)" >&2
        exit 2
        ;;
esac

echo "[models] LULUS"
