# Step 14a part 0, step 4: style tile, Performance mock, nav options (report, 2026-11-15)

Mock workspace: `docs/design/mock/` (`npx vite build`, then `npx vite preview`, served on `http://127.0.0.1:5179/` only).
Built with `design-system/MASTER.md`, the Cocoa Amber theme and fonts B (Instrument Serif, Geist, Geist Mono). Every
number on the pages is **MOCK DATA** and labelled so. Nothing is committed. The videos live in the gitignored
`docs/design/video/` on the build PC, so their links below work only there; the screenshots are in `docs/design/step4/`.

Open the pages live: `?view=performance`, `?view=tile`, `?view=home`; `&nav=waterline` or `&nav=dock` switches the
nav, `&tab=drawdown|montecarlo|calendar|trades` opens a Performance tab, `&calm=1` shows Calm, `&intro=1` replays
Home's opening sequence.

## What I built

**Style tile** (`?view=tile`): brand (logo, wordmark, favicon), type scale, the colour tokens, the risk-first
controls (FLAT ALL, status pills, alert banners), form controls (segmented tabs, risk slider with its cap, toggle,
inputs with an error, Save / Cancel / Hold to delete), and one card for each showpiece: tilt account card, border beam
+ spotlight, delta number ticker, flip-disk next setup window, chart palette (candles, Pika / Barney orbs, King ring),
Burst on "Request payout", and the Dot Matrix Text empty state.

**Performance mock** (`?view=performance`): title, filters (account, ledger period, dates, $ | R, Export CSV), six KPI
tiles (net P&L, net R, trades, win rate, profit factor, max drawdown) with sparklines and the change of the last
month vs the average of the earlier months, and five tabs:
- Equity (Lightweight Charts, crosshair tooltip),
- Drawdown (with the drawdown limit line),
- Monte Carlo (1,000 reshuffles by whole days: 5–95% and 25–75% bands, median, the smallest and largest drawdown
  paths, the backtest order, the limit line, the chance that the drawdown reaches the limit),
- Calendar (P&L by day with week totals, hover cards, a day's trades in a side panel, month change),
- Trades (sortable, filterable, sticky header).

The Halden dot-matrix "CreamFlow" footer closes the page.

**Three nav options**, all on the Performance page:
1. **Sidebar:** collapsible; it becomes icons-only below 1100 px.
2. **Waterline:** a liquid-glass bar (21st.dev 43059, recoloured cream and caramel for the dark theme). It sits
   centred under the ticker and shrinks to icons below 1280 px.
3. **Floating dock** (21st.dev 18014): bottom centre; the icons grow as the pointer passes.

**21st.dev parts used:** KPI Tile 40086, Delta Number Ticker 44850, Animated Tabs 24930, Border Beam 23408,
Spotlight 35180, Burst 33152, Tilt Card 12244, Flip-Disk 24220, Dot Matrix Text 25484, the Halden footer 37628,
Waterline 43059 and Floating Dock 18014. I read each one's code before use: none makes network calls or loads
remote assets.

## Screenshots

Whole page; the top bar never wraps at any width.

| Page | 1440 | 1280 | 1024 | 960 |
|---|---|---|---|---|
| Style tile | [tile_1440](step4/tile_1440.png) | [tile_1280](step4/tile_1280.png) | [tile_1024](step4/tile_1024.png) | [tile_960](step4/tile_960.png) |
| Performance (Equity) | [performance_1440](step4/performance_1440.png) | [performance_1280](step4/performance_1280.png) | [performance_1024](step4/performance_1024.png) | [performance_960](step4/performance_960.png) |

Other Performance tabs at 1440: [Drawdown](step4/performance_drawdown_1440.png),
[Monte Carlo](step4/performance_montecarlo_1440.png), [Calendar](step4/performance_calendar_1440.png),
[Calendar with a day open](step4/performance_calendar_day_1440.png), [Trades](step4/performance_trades_1440.png).

Nav options (first screen only):

| Option | 1440 | 960 |
|---|---|---|
| Sidebar | [nav_sidebar_1440](step4/nav_sidebar_1440.png) | [nav_sidebar_960](step4/nav_sidebar_960.png) |
| Waterline | [nav_waterline_1440](step4/nav_waterline_1440.png) | [nav_waterline_960](step4/nav_waterline_960.png) |
| Floating dock | [nav_dock_1440](step4/nav_dock_1440.png) | [nav_dock_960](step4/nav_dock_960.png) |

## Videos

1440 × 900. Headless recordings don't show the cursor: the hover effects show where it is. Every recording had no
blocked requests and no console errors or warnings.

- [step4_performance.webm](video/step4_performance.webm) (≈ 60 s): staggered page entrance, KPI numbers rolling in,
  spotlight and sparkline hover, equity crosshair, $ ↔ R, every tab, calendar hover, day panel and month change,
  trades sort and filter, the dot-matrix footer, and the page transition to Home and back.
- [step4_nav_options.webm](video/step4_nav_options.webm) (≈ 40 s): sidebar, Waterline, floating dock.
- [step4_tile.webm](video/step4_tile.webm) (≈ 40 s): slider, toggle, tilt card, border beam, delta ticker,
  flip-disk, Burst, dot-matrix empty state.
- [home_intro.webm](video/home_intro.webm) (≈ 14 s): Home's restored opening sequence (see below).

The full list of design videos is in [videos.md](videos.md).

## Fixes made while checking

- **Home node entrance restored** (your request). On first open the wordmark forms alone (≈ 2.5 s). Then the six
  nodes arrive one by one, clockwise from Live (Live, Review, Performance, Settings, Accounts, Gameplan, one every
  200 ms), each line drawing out from its node towards the word; all six take ≈ 1.7 s.
  - Returning to Home in the same session (a reload in the same tab counts) shows the formed scene with a 200-ms fade.
  - A click or key press during the intro skips it; a node that hasn't arrived yet can't be clicked.
  - Calm shows everything formed at once.
  - `&intro=1` replays the intro on every open.
  - **Cause:** the page-to-page transition added in step 4 blocked the entrance animation of anything that appears
    later on the first page loaded. That also hid the Performance page's staggered entrance; both work again.
  - The rules are in `design-system/pages/home.md`.
- **Mock risk:** I lowered it from $500 to $250 a trade. At $500 the mock's largest drawdown ($3,750) was beyond the
  $2,500 limit, and the Monte Carlo showed a 94.5% chance of hitting it. Now net is +$20.8k, the largest drawdown
  −$1.9k and that chance 22.5%.
- **Monte Carlo fan:** it now has $ and month labels and a "backtest order" legend entry, and the chance is worded
  "drawdown reaches $2,500".
- **KPI tiles:** the change badge always sits under the value in a narrow tile, so one row never mixes the two
  layouts.
- **Dot Matrix Text:** its canvas grew during a recording and covered the cards around it. I fixed that in the
  component, and the comment in its file explains the change.
- **Style tile at 1280 px:** "Hold to delete" wrapped and the type card was crowded; both fixed.

## Still open

- **Inline styles and CSP:** the mock allows inline styles; the Waterline bar and the border beam inject theirs.
  In the real panel they move into the bundled CSS, so `default-src 'self'` holds.
- **Sidebar on the style tile:** it highlights Home there, because the tile isn't a real page (mock only).
- **Dot Matrix Text** redraws every frame even when the text is still. That is fine for an empty state; it gets a
  stop-when-settled fix if you keep it.
- **Home concept:** the mock's order-flow wordmark is a prototype. At the build it comes from its 21st.dev base
  (Pixel Flow Field 30145).

## What I need you to pick

1. **Nav option:** sidebar, Waterline or floating dock.
2. **Showpieces to keep:** tilt account card, border beam + spotlight, delta ticker, flip-disk next window, Burst on
   payout requested, Dot Matrix Text empty state, Halden dot-matrix footer.
3. **Performance page:** any changes to the KPI set, the tabs, the Monte Carlo, the calendar or the trades table.
