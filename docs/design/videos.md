# Design videos (Playwright recordings)

Videos are kept out of git (owner 2026-11-14, SPEC §13 Q-step14a (6)): they live in the gitignored folder
`docs/design/video/` of the repo on the machine that recorded them (the build PC). Only screenshots are committed.
Each video is 1440 × 900, recorded from the built design workspace (`docs/design/mock/`, `npx vite build` then
`npx vite preview`, served on 127.0.0.1 only; the recorder blocks every other request and reported none, and no
console errors).

| Video | Shows | Recorded | Screenshots |
|---|---|---|---|
| `docs/design/video/home_pour.webm` | Home concept 1, **cream pour**: a stream of cream falls into the word and the letters fill from the bottom with a wavy surface (intro ≈ 2.2 s), then 21st.dev Liquid Text (isaiahbjork, 43190) idles and the cursor pulls beads off the strokes; nodes and flowing lines appear one after another; hover cards on Performance and Accounts; key 6 | 2026-11-14 | `home_pour_1440.png`, `home_pour_1024.png`, `home_pour_calm_1440.png` |
| `docs/design/video/home_flow.webm` | Home concept 2, **order flow (picked)**: gold, lavender and cream tick particles stream in from both edges like a tape and lock into the larger title (cream "Cream", gold "Flow"); a slow flow field moves through them at rest, the cursor pushes them aside, faint ticks keep crossing the scene; nodes appear, hover cards on Performance and Accounts, key 6 | 2026-11-14 (re-recorded after the owner's changes) | `home_flow_1440.png`, `home_flow_1280.png`, `home_flow_960.png`, `home_flow_hover_1440.png`, `home_flow_hover_960.png`, `home_flow_calm_1440.png`, `home_flow_theme_{original,thuray,amber,cocoa}_1440.png (the comparison before the pick)` |
| `docs/design/video/home_trace.webm` | Home concept 3, **price trace**: a glowing gold line draws each letter left to right, then the letters fill with cream; behind, a GEX-style heat glow of gold (Pika) and lavender (Barney) node glows pulses, the King brightest with a ring, and a faint price line keeps drawing; the cursor warms the glows near it | 2026-11-14 (re-recorded after the stroke fix) | `home_trace_1440.png`, `home_trace_1024.png`, `home_trace_calm_1440.png` |
| `docs/design/video/home_intro.webm` (≈ 14 s) | Home's **opening sequence** restored (owner 2026-11-15; `docs/design/mock/record-intro.mjs`): first open, the wordmark forms alone, then the six nodes arrive clockwise from Live, each line drawing out to the word (≈ 1.7 s); Performance and back to Home: the formed scene with a short fade; then `&intro=1` replays the intro and a click skips it | 2026-11-15 | — |

Resize checks (owner 2026-11-14; `docs/design/mock/shot-resize.mjs`, screenshots only): `home_resize_960_to_1440_a_loaded_960.png` → `home_resize_960_to_1440_b_resized_1440.png`, and `home_resize_1440_to_960_a_loaded_1440.png` → `home_resize_1440_to_960_b_resized_960.png`.

Concept 1 runs the 21st.dev component itself (with its four helper files written locally, since `get_component`
doesn't return them). Concepts 2 and 3 are quick prototypes of the same techniques as their leading 21st.dev candidates
(Pixel Flow Field 30145, Stroke Draw Type 39119; `design-system/shortlist/04-home-wordmark-themes-categories.md`); the
picked concept is built from its 21st.dev component at step 14.

## Step 4: style tile, Performance mock, nav options (2026-11-15)

Recorded by `docs/design/mock/record-step4.mjs` (1440 × 900; no blocked requests, no console errors or warnings).
They predate the final round (`step4_final.md`): the Performance video shows the old KPI row (Net R) and a fixed
$2,500 limit, and the nav video the earlier dock; kept as they are (owner 2026-11-15), the final screenshots show the
current state.
Headless recordings show no cursor: the hover effects show where the pointer is. All numbers are MOCK DATA.

| Video | Shows |
|---|---|
| `docs/design/video/step4_performance.webm` (≈ 60 s) | Performance: page enters with a stagger, KPI values roll and sparklines draw; spotlight follows the pointer over the six KPI tiles; sparkline scrub on Net P&L; equity crosshair and tooltip; $ ↔ R switch; Drawdown, Monte Carlo, Calendar tabs (sliding underline); calendar day hover cards, a day's side panel, month back twice (slide); Trades: sort by Net both ways, filter "short"; scroll to the Halden dot-matrix footer and move the pointer through it; page transition to Home and back |
| `docs/design/video/step4_nav_options.webm` (≈ 40 s) | The three nav options on Performance: (1) **sidebar** (hover, Accounts placeholder and back), (2) **Waterline** liquid-glass bar (43059, retinted cream / caramel; the drop moves and pops on each pick), (3) **floating dock** (18014; magnification sweep, picks) |
| `docs/design/video/step4_tile.webm` (≈ 40 s) | Style tile: risk slider drag with its cap, breakeven toggle, the tilt account card, border beam + spotlight panel, the delta ticker updating, the flip-disk next-window display, Burst on "Request payout" twice, Dot Matrix Text empty state cycling |

Screenshots (`docs/design/step4/`, from `docs/design/mock/shot-step4.mjs`; whole page; the top bar never wraps):

- Style tile: `tile_1440.png`, `tile_1280.png`, `tile_1024.png`, `tile_960.png`
- Performance (Equity tab): `performance_1440.png`, `performance_1280.png`, `performance_1024.png`, `performance_960.png`
- Performance, other tabs at 1440: `performance_drawdown_1440.png`, `performance_montecarlo_1440.png`, `performance_calendar_1440.png`, `performance_calendar_day_1440.png` (a day's side panel open), `performance_trades_1440.png`
- Nav options (first screen only): `nav_sidebar_1440.png`, `nav_sidebar_960.png`, `nav_waterline_1440.png`, `nav_waterline_960.png`, `nav_dock_1440.png`, `nav_dock_960.png`

Open them live: `npx vite preview` in `docs/design/mock/`, then `http://127.0.0.1:5179/?view=performance` or
`?view=tile`; `&nav=waterline` / `&nav=dock` switches the nav, `&tab=montecarlo` (etc.) opens a tab, `&calm=1` shows Calm.

## Step 4 round 2 (2026-11-15)

| Video | Shows |
|---|---|
| `docs/design/video/step4_dock_minimize.webm` (≈ 14 s) | The floating dock minimizing into one circle and expanding again: `&dockmin=centre` (the circle stays at the bottom centre), then `&dockmin=corner` (it moves to the bottom-right corner). From `docs/design/mock/shot-round2.mjs` |

Round-2 screenshots: `docs/design/step4/round2/` (summary in `docs/design/step4_round2.md`).
