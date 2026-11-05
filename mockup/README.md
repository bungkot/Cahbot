# mockup/ — Cahbot UI mockup (Swiss Modernist)

A self-contained static mockup to validate the UI/UX vision **before** the pipeline is built (Sprint 1 in `ROADMAP.md`). The design follows `docs/conventions/ui.md`.

## Running

```bash
# option 1: open directly in a browser
start mockup/index.html          # Windows

# option 2: run the Playwright e2e (chromium)
uv run playwright install chromium   # once
make test-e2e                        # pytest -m e2e
```

## What is simulated

| Demo | Design intent |
|---|---|
| Empty thread | A canvas starting at zero — a question triggers the first answer |
| Sidebar | 1 active session (`Hari ini`) + empty document list + upload dropzone |
| Button `demo: jawapan + petikan` | The multi-source answer pattern — inline `[n]` citations at 8pt + 8pt SUMBER footnotes |
| Button `demo: penolakan` | Refusal — red strip, no citations |
| composer | The ask flow (the mockup answers `mockup`); ANDA messages right-aligned, CAHBOT left-aligned |

Header: `CAHBOT` logotype + `Retrieval Augmented Generation` tagline.

Mockup out-of-scope: token streaming, Chainlit integration, real data — that is Sprint 5 (per the ADR D6-update decision).
