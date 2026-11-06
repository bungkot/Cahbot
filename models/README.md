# models/

Place model files here (NOT committed, NOT baked into the image):

- `Qwen3-1.7B-Q3_K_M.gguf` — main LLM (llama.cpp, CPU; q3_k_m, 939 MB — muat di RAM host 8 GB).
- `multilingual-e5-small.onnx` — 384-dim embedding (with its associated tokenizer).

Download from official sources (Hugging Face) and verify checksums before use. Paths are configured in `.env` (`MODEL_PATH`, `EMBED_MODEL_PATH`).
