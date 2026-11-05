# Design System — Swiss Modernist (Cahbot UI)

The style source for the whole UI (mockup + production). Principles: **legibility, grid, structural honesty** — function shows as structure, not decoration.

## 1. Principles

1. **Strict grid** — every element interacts on a modular column grid (12 columns desktop, 1 column <720px); thin lines as separators, NOT shadowed cards.
2. **Typography = hierarchy** — sizes/weights are deliberate: only 4 scales (display/H1 32–40, H2 20–24, body 15–16, meta 12–13 monospace). No extra decoration.
3. **Limited color** — white/carbon (`#0A0A0A` on `#FFFFFF`) + **1 red accent** `#E30613` (Swiss International Red) reserved for: the primary action (send), failed status indicators, active-section markers. Grey #F4F4F5 for secondary surfaces.
4. **Strict shapes** — border-radius 0 (or at most 2px), no drop shadows, no gradients, no emoji in the production UI (geometric 1.5px line icons if needed).
5. **8pt spacing** — every spacing step is a multiple of 8 (8/16/24/32/48); text margins follow the columns.
6. **Monospace meta** — citations/status/metadata (timestamps, tokens, latency) treated like "technical notes": mono font 12px, lowercase, no uppercase, grey color.

## 2. Core components (visual contract)

| Component | Swiss rule |
|---|---|
| Header/masthead | Top BLOCK: logotype name `CAHBOT` weight 700 + small mono tagline on a second line; 2px black underline |
| Sidebar | Fixed left column, 1px grey vertical line; session list = plain text list, active item marked with a 2pt red block on its left edge |
| User message | No 3D bubble: a text block with the mono label `ANDA` + a top line; very light grey background (#F8F8F8) |
| Assistant message | Mono label `CAHBOT` on the left; clean text, sentences follow the grid; with numbered footnote citations `[1]` |
| Citations | Footnote under the answer: a numbered list — `1. source.pdf — section 4 (1-line snippet)`; mono, grey; hover → white block with a 1px black outline |
| Refusal | A message line with a 2pt red strip on the left + the refusal message "Maaf, saya tidak menjumpai maklumat tersebut dalam dokumen yang tersedia." (artifact string) — no citations |
| Chat input | Full-line 1px black textfield; Send button = solid red block, white text, radius 0 |
| Ingest status | `mono` badge with a 6px dot: green (done)/yellow (running)/red (failed)/grey (pending); no excessive animation |
| Dropzone | 1px grey dashed area with mono text; hover → solid red |

## 3. Typography

- Sans: **Inter** (fallbacks: Helvetica Neue, Arial) — all text.
- Mono: **IBM Plex Mono** (fallback: ui-monospace) — all meta/citations/labels.
- Letter-spacing: mono labels +6%; headings -1%.
- Line-height: body 1.55; headings 1.15.

## 4. Accessibility & technical

- Text contrast ≥ 4.5:1 (red only for small non-text elements/accents).
- Keyboard focus: 2px red outline, offset 2px (visible, no blur).
- All interactions (send, citation click, file drop) must carry ARIA state: `role="log"`, `aria-live="polite"` on the thread; explicit labels on inputs.
- Responsive: sidebar becomes a <details> on mobile; the thread stays a single-grid column.
- No raster images; no heavy web font bundles — local CSS + system fallbacks.

## 5. Applied bare

- Mockup: `mockup/index.html` (one self-contained, valid file).
- The production UI implementation (Sprint 5) must use the exact same CSS (carried into components), not a color re-imitation.
