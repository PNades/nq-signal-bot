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
| The account's real drawdown rule | the drawdown chart's floor line and the Monte Carlo's chance follow the selected account's own rule (static or EOD-trailing, with its lock: Lucid's floor lock at $25,100 once the floor gets there or at the first payout, Pro's lock at the first payout), never a fixed amount |
| One account at a time on Drawdown and Monte Carlo | the account switcher sits in the page-tab row, centred, after a faint divider: the same underline tabs, one per account with its name and a small type badge; fades in on Drawdown / Monte Carlo, stays between them, fades out elsewhere; ← → switch accounts; it follows the page's account filter (and moves it); a compact dropdown in the same spot when the row can't hold it (2026-11-15; a Waterline bar was tried and dropped) |
| Mock accounts | mffur1 MFFU Rapid EOD 25k evaluation, mffub1 MFFU Builder 25k funded, lucid1 Lucid Flex 25k funded, each with its SPEC §6.7 preset rules (2026-11-15) |
| Account filter | every view (KPIs, equity, calendar, trades) follows it |

## Panel build, stage 1 changes (2026-11-15)

| Pick | What it means |
|---|---|
| KILL dialog | 21st.dev Alert Dialog Layout 28277, its split layout: a crit-tinted band with the icon and the title, then what KILL does line by line (SPEC §9c `!kill`, word for word), a note when no engine runs here, Cancel (Esc) and KILL (Enter); focus on KILL at open, Esc / Cancel / backdrop back out, never hold-to-confirm, appears at once |
| Banners | info banners slide down with a short fade and fade out after ~5 s, paused while the pointer is over them, "Got it" closes at once; warning and critical banners never fade by themselves; critical ones appear without motion (MASTER §7) |
| Notification history | a bell in the top bar (before KILL): every banner and toast shown in the tab, newest first, tabs All / Alerts / Info, unread count (crit-red while a critical one is unread); the panel opens below the banner stack, never over it |
| Date block | the old sidebar's date block (serif day number, "NOV 2026 / SUN · EASTERN") fixed in the bottom-left corner of every page, Home included; pages keep at least 76 px of bottom room; hidden below 900 px |
| Command palette | no outline on its search field (a faint line under it only); its top sits so the palette is centred slightly above the middle (46% of the height) and stays put while typing |
| KILL dialog: the band variant (2026-11-15) | picked over "ledger" and "seal" (`docs/design/build/stage1c/`): the red gradient band with a solid red badge and the serif title, the list with count chips; it sits ~26 px above the vertical centre; Esc closes it wherever focus is; a click on its text keeps focus on KILL; an outside click does nothing |
| Focus rings (2026-11-15) | only for keyboard navigation: hidden while the last input was the mouse |
| Dock magnification (2026-11-15) | up to 1.2× (40 → 48 px), a third of the earlier jump; the hover labels draw above the unsaved-changes bar |
| Unsaved-changes bar (2026-11-15) | follows the dock: 118 px from the bottom while the dock is open (clear of its hover labels), 16 px while it is minimized; slides 220 ms both ways, instant in Calm; buttons Reset (undo, stay), Discard (undo, close the editor; a new account creates nothing), Save / Add account |
| Own presets (2026-11-15) | a delete control on each of the owner's presets in the picker (built-in ones have none), a quick inline confirm, then a toast with Undo for 10 s |
| Account switcher (2026-11-15) | a real dropdown: opens on hover after ~150 ms (stays open over the trigger or the list), on click and from the keyboard; every account, scrollable past ~5, each with its name, type badge and stage, status (active / paused / blown / inactive, "draft — not checked" muted), room to the floor with a thin bar (amber below 50%, red below 25%), today's P&L, progress to the profit target or next payout, lives left ("—" until the engine, step 22); "Manage accounts" and "+ Add account" at the bottom; a pick sets the panel's focus only, never which accounts trade |
| Home, Accounts, dock, dropdowns (2026-11-15) | Home's hint shares the date block's line; when a banner comes or goes, Home is laid out once and glides there (250 ms, FLIP); the wordmark runs at 60 fps idle in Full (MASTER §8 exception); node cards open towards the wordmark and stay inside the scene, opaque over their lines; the accounts list and the editor fade and slide into each other (220 ms, reversed on close); the dock grows by scale transforms on springs in fixed slots; a click pins the account dropdown open; nothing stays highlighted after the pointer leaves a list |
| Overlays, hints, Home stillness (2026-11-15) | an open account dropdown or command palette makes the page behind it inert: outside clicks only close it, no hover behind it, KILL and FLAT ALL always act on the first click; the palette's backdrop leaves the top bar sharp; no native title tooltips (our styled hint instead); no horizontal scrollbar on any page; Home's node headers are fixed on their points with the cards as overlays, no layout animation, and the scene re-lays out only on a change of 1 px or more |
