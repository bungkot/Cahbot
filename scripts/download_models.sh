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
#   LLM_REPO    default Qwen/Qwen3-4B-GGUF (repo GGUF official Qwen — nama
#               "Instruct-2507-GGUF" TIDAK ada di kanal GGUF resmi)
#   LLM_FILE    default Qwen3-4B-Q4_K_M.gguf (~2.6 GB)
#   E5_CANDIDATES  spasi-daftar repo e5 (di-probe berurutan; pertama yang
#                  merespons 200 dipakai) — default onnx-community/...-ONNX,
#                  Xenova/multilingual-e5-small, onnx-community/multilingual-e5-small
#   HF_TOKEN    opsi (GitHub Secrets) — bila kosong ≥ coba anonim. Runner CI
#               (IP datacenter) sering 401 anon: isi read-token → lolos.
#
# Jalur utama = CLI `hf` (huggingface_hub via uvx): UA sah + CDN redirect + auth.
# Jatuh ke curl (dengan token bila ada) bila hk tidak tersedia/gagal — setiap
# kegagalan mundur = fail keras (skip hening dilarang).
#
# Cache di Actions: job harus cache $MODELS_DIR via actions/cache; skrip ini
# SKIP unduhan bila file target sudah ada (cache hit = lebih cepat).
set -euo pipefail

MODE="${1:-all}"
MODELS_DIR="${MODELS_DIR:-models}"
LLM_FILE="Qwen3-4B-Q4_K_M.gguf"
LLM_REPO="${LLM_REPO:-Qwen/Qwen3-4B-GGUF}"

# Kandidat repo e5 (nama ONNX berubah di HF onnx-community/Xenova) — di-
# probe urut bila token; yang pertama merespons 200 dipakai nama tersebut.
E5_CANDIDATES="${E5_CANDIDATES:-onnx-community/multilingual-e5-small-ONNX Xenova/multilingual-e5-small onnx-community/multilingual-e5-small}"

# env alias lama (HUGGING_FACE_HUB_TOKEN) turut di-respec oleh hub CLI.
export HF_TOKEN="${HF_TOKEN:-${HUGGING_FACE_HUB_TOKEN:-}}"
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN:-${HUGGING_FACE_HUB_TOKEN:-}}"

_hf_curl() { # _hf_curl <url> — keluaran skanner lewat stdout
    if [ -n "${HF_TOKEN}" ]; then
        curl -fsS -H "Authorization: Bearer ${HF_TOKEN}" "${1}"
    else
        curl -fsS "${1}"
    fi
}

_find_repo() { # _find_repo "<repo1 repo2 ...>" — echo repo pertama yang 200
    for repo in $1; do
        if _hf_curl "https://huggingface.co/api/models/${repo}" > /dev/null 2>&1; then
            echo "${repo}"
            return 0
        fi
    done
    return 1
}

_hf_fetch() { # <repo_id> <path-dalam-repo> <dest-file>
    local repo="$1"
    local path="$2"
    local dest="$3"
    if [ -s "${dest}" ]; then
        echo "[models] sudah ada, di-skip: ${dest}"
        return 0
    fi
    mkdir -p "$(dirname "${dest}")"
    echo "[models] unduh hub: ${repo} :: ${path}"
    local staging
    staging="$(mktemp -d)"
    local cli_ok=1
    if command -v hf > /dev/null 2>&1; then
        if ! hf download "${repo}" "${path}" --local-dir "${staging}" > /tmp/hf.log 2>&1; then
            cli_ok=0
        fi
    elif uvx --from huggingface_hub hf download "${repo}" "${path}" --local-dir "${staging}" > /tmp/hf.log 2>&1; then
        : # sukses via uvx (runner uv tersedia)
    else
        cli_ok=0
    fi
    if [ "${cli_ok}" = "1" ] && [ -s "${staging}/${path}" ]; then
        mv "${staging}/${path}" "${dest}"
        echo "[models] ok (hub): ${dest}"
        return 0
    fi
    echo "[models] jalur hub GAGAL — coba fallback curl (rincian):"
    tail -n 15 /tmp/hf.log 2> /dev/null || true
    # Fallback curl — bila token ADA dikeheaderkan (401 anon di runner cloud).
    local auth=()
    if [ -n "${HF_TOKEN}" ]; then
        auth=(-H "Authorization: Bearer ${HF_TOKEN}")
    fi
    curl -fL --retry 3 --retry-delay 2 \
        -H "User-Agent: cahbot-ci/1.0 (huggingface-hub)" \
        "${auth[@]}" \
        -o "${dest}.part" \
        "https://huggingface.co/${repo}/resolve/main/${path}"
    mv "${dest}.part" "${dest}"
    echo "[models] ok (curl): ${dest}"
}

fetch() { # fetch <repo-id> <path-dalam-repo> <dest-file> — idempoten di _hf_fetch
    _hf_fetch "$1" "$2" "$3"
}

case "${MODE}" in
    llm)
        fetch "${LLM_REPO}" "${LLM_FILE}" "${MODELS_DIR}/${LLM_FILE}"
        ;;
    e5)
        # FP32 (onnx/model.onnx) — IDENTIK dengan model di dev host (~450 MB):
        # ambang kosinus penolakan (0.82) diukur atas e5 fp32; varian
        # quantized akan menggeser skor dan mencemari gerbang eval.
        # Repo e5 di-probe berurutan (nama ONNX cides di onnx-community/Xenova);
        # fail keras dengan daftar nama yang dicoba supaya mudah dikoreksi.
        e5_repo="$(_find_repo "${E5_CANDIDATES}")" || {
            echo "[models] GAGAL: tidak ada repo e5 valid di kandidat: ${E5_CANDIDATES}" >&2
            exit 3
        }
        echo "[models] repo e5 terpilih: ${e5_repo}"
        fetch "${e5_repo}" "onnx/model.onnx" "${MODELS_DIR}/e5/onnx/model.onnx"
        fetch "${e5_repo}" "tokenizer.json" "${MODELS_DIR}/e5/tokenizer.json"
        ;;
    all)
        fetch "${LLM_REPO}" "${LLM_FILE}" "${MODELS_DIR}/${LLM_FILE}"
        e5_repo="$(_find_repo "${E5_CANDIDATES}")" || {
            echo "[models] GAGAL: tidak ada repo e5 valid di kandidat: ${E5_CANDIDATES}" >&2
            exit 3
        }
        echo "[models] repo e5 terpilih: ${e5_repo}"
        fetch "${e5_repo}" "onnx/model.onnx" "${MODELS_DIR}/e5/onnx/model.onnx"
        fetch "${e5_repo}" "tokenizer.json" "${MODELS_DIR}/e5/tokenizer.json"
        ;;
    *)
        echo "[models] mode tidak dikenal: ${MODE} (gunakan all|llm|e5)" >&2
        exit 2
        ;;
esac

echo "[models] LULUS"
