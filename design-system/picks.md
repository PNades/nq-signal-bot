# CreamFlow: the owner's picks (step 14a part 0)

> One place for every design pick the owner has made, with its date. The rules behind each pick live in
> `MASTER.md` and `pages/*.md`, and SPEC §13 Q-step14a records the decisions. Anything not listed here is still a
> proposal. The mock that shows each pick is `docs/design/mock/`, with the reports `docs/design/step4_report.md`,
> `step4_round2.md` and `step4_final.md`.

## Stack and safety (2026-11-14)

| Pick | What it means |
|---|---|
| React single-page app | Vite + TypeScript + Tailwind + shadcn/Radix + Motion + Lightweight Charts, served with a JSON API and server-sent events by Starlette + uvicorn on 127.0.0.1 only; Streamlit dropped |
| Builds per machine | `nqbot ui build`: `npm ci` from the committed lockfile only, `npm audit`, Vite build into the gitignored `ui/web/dist/` |
| Kill / flat never depend on the React app | no-JavaScript `/emergency` page from the Python server, CLI `nqbot kill` / `nqbot flat` (step 22), Discord `!kill` / `!flat` |
| Nothing remote at runtime | `Content-Security-Policy: default-src 'self'`; fonts self-hosted; every 21st.dev component's code read first |
| Videos outside git | gitignored `docs/design/video/`, listed in `docs/design/videos.md`; only screenshots are committed |

## Look (2026-11-14)

| Pick | What it means |
|---|---|
| Fonts: pairing B | Instrument Serif (display), Geist (UI), Geist Mono (numbers, eyebrows) |
| Theme: Cocoa Amber | as previewed, including its `#111111` background (tokens in `MASTER.md` §3) |
| Levels and zones | a single level is a thin line; a zone (merged levels) is a shaded band |
| GEX radial | Pika gold for positive and Barney lavender for negative, `+` / `−` in the labels; the King is the brightest orb with an extra glow ring, the same on the main chart, never orange |
| Brand colours for motion | moving particles and ticks in cream, gold and lavender; green and red only for P&L and status |
| Top bar | never wraps; FLAT ALL always on one line and fully visible; `Ctrl K`, never `⌘ K` |

## Home (2026-11-14 to 2026-11-15)

| Pick | What it means |
|---|---|
| Concept: order flow | tick particles stream in from both edges and lock into the wordmark (cream "Cream", gold "Flow"); build base Pixel Flow Field 30145 |
| Re-layout on every resize | the whole scene is laid out again from the content area's size |
| Connecting lines: "arcs to the centre" | the four corner lines mirrored left-right and top-bottom, a fixed gap before the letter in front |
| Node hover cards | 6 px from label to value, 7 px from value to sub-line |
| Opening sequence (restored 2026-11-15) | the wordmark forms alone, then the nodes arrive one after another, clockwise from Live, each line drawing out to the word (about 1.7 s); later visits in the session show the formed scene with a 200-ms fade; a click or key skips it; Calm shows it formed |
| Home approved | 2026-11-15; layout details in `pages/home.md` only |
| Messages in the bottom-right corner | Home's toasts and key hint sit in one row in the bottom-right corner, below the nodes (2026-11-15); the mock's Performance node and key 6 open the Performance page |

## Navigation (2026-11-15)

| Pick | What it means |
|---|---|
| Floating dock (21st.dev 18014) | over the sidebar and the Waterline bar |
| Not on Home | Home's six nodes, keys 1–6 and `Ctrl K` cover navigation there |
| Minimize circle: **corner** | a circle at the end of the dock collapses it into one circle in the bottom-right corner, and back, with a short animation; remembered for the session |
| Bottom padding | every page reserves the dock's footprint, so nothing stays behind it when scrolled to the bottom |
| Toasts never under the dock | bottom-right, above the dock's footprint whichever variant (2026-11-15) |
| The dock may float over the footer letters | its glass stays dark enough (90% `#111111` with blur) that its icons stay clearly readable over the cream dots |

## Showpieces: all kept (2026-11-15)

| Showpiece | 21st.dev | Where | Rule |
|---|---|---|---|
| Tilt account card | Tilt Card 12244 | Accounts cards | 6°, scale 1.02; off in Calm and when the account is in danger |
| Border beam + spotlight | Border Beam Panel 23408, Spotlight 35180 | one highlighted panel per page | **border beam on only one element per page** |
| Delta number ticker | Delta Number Ticker 44850 | live P&L numbers | rolls between values in Full, snaps in Calm |
| Flip-disk next window | Flip-Disk Matrix 24220 | the next setup window | never used for risk numbers |
| Burst | Burst 33152 | evaluation passed, payout requested | a static badge in Calm |
| Dot Matrix Text | Dot Text 25484 | empty states, splash | **stops redrawing once settled**; wakes only for a text change or a resize |
| Dot-matrix footer | Halftone Wordmark Footer 37628 | the end of a page | the word "cream" in Geist 900 at full screen down to 600 at 960 px, sized from its own box, the page ending inside the letters (2/7 ≈ 29% of their height hidden by the bottom edge, hidden to visible 2:5, owner 2026-11-15) |

Also in use: KPI Tile 40086, Animated Tabs 24930.

## Performance page (2026-11-15)

| Pick | What it means |
|---|---|
| KPI tiles, in this order | Net P&L, Trades, **Net R per trade** (average net R per trade; replaces Net R), Win rate, Profit factor, Max drawdown |
| The account's real drawdown rule | the drawdown chart's floor line and the Monte Carlo's chance follow the selected account's own rule (static or EOD-trailing, with its lock: Lucid's floor lock at $25,100 once the floor gets there or at the first payout, Pro's lock at the first payout), never a fixed amount; with All accounts, one panel per account |
| Account filter | every view (KPIs, equity, calendar, trades) follows it |
