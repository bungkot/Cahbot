# models/

Place model files here (NOT committed, NOT baked into the image):

- `Qwen3-1.7B-Q3_K_M.gguf` — main LLM (llama.cpp, CPU; q3_k_m, 939 MB — muat di RAM host 8 GB).
- `multilingual-e5-small.onnx` — 384-dim embedding (with its associated tokenizer).

Download from official sources (Hugging Face) and verify checksums before use. Paths are configured in `.env` (`MODEL_PATH`, `EMBED_MODEL_PATH`).

## CI (GitHub Actions — tanpa Docker dev container)

Runner Actions (ubuntu-latest, 16 GB RAM) memuat model yang LEBIH BESAR secara
otomatis via `scripts/download_models.sh` (idempoten + `actions/cache`):

- `Qwen3-4B-Q4_K_M.gguf` (~2.6 GB, repo resmi `Qwen/Qwen3-4B-GGUF`) — LLM mutu lebih tinggi untuk
  smoke/e2e di CI; `MODEL_PATH` di-set lewat env workflow (tidak mengubah dev host).
- `e5/onnx/model.onnx` + `e5/tokenizer.json` — fp32 SAMA dengan file
  `multilingual-e5-small.onnx` di atas (ambang kosinus penolakan tetap baku).

LLM 1.7B di host tetap default; kalau nanti mau memakai 4B di host/prod, cukup
ubah `MODEL_PATH` + pastikan RAM memungkinkan — tidak ada migrasi skema (embedding tidak berubah).
