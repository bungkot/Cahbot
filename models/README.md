# models/

Place model files here (NOT committed, NOT baked into the image):

- `qwen3-1.7b-q4_k_m.gguf` — main LLM (llama.cpp, CPU).
- `multilingual-e5-small.onnx` — 384-dim embedding (with its associated tokenizer).

Download from official sources (Hugging Face) and verify checksums before use. Paths are configured in `.env` (`MODEL_PATH`, `EMBED_MODEL_PATH`).
