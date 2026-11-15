# Home (new start page; owner 2026-11-14, added to SPEC §9b)

> Overrides MASTER.md for this page. **Concept picked by the owner 2026-11-14: order flow** (concept 2 below).

## Layout override
- The whole content area is **one full-screen CreamFlow scene** (sidebar, top bar and ticker tape stay). No KPI row,
  no tabs, no tiles. Fits one screen without scrolling at 1280 × 800 and up.
- The title (brand display face, "Cream" upright in cream, "Flow" italic in gold) sits at the optical centre, slightly
  above the middle, at `min(20% of the scene width, 300 px)` (owner 2026-11-14: larger, close to the pour concept's size).
  The nodes: Live and Settings on the vertical centre line at fixed shares of the scene (8% and 90% of its height);
  the four corner nodes placed relative to the wordmark, not the screen (owner 2026-11-15): 0.48 of the word's width
  either side of its centre and 1.28 font sizes above / below the middle of the lowercase body (their place at
  960 px wide, the owner's reference), kept inside the scene (`cornerNodePos` in the mock's `titleLayout.ts`).
  **Connecting lines (owner 2026-11-15, "arcs to the centre"):** every line is a gentle curve from its node, aimed
  at a point inside the word so its last stretch points at the letter in front of it, and it ends a gap before
  that letter measured along the line: 35% of the capital-letter height (`LINE_LETTER_GAP_CAP_SHARE`; about 60 px
  at 1440 px wide, 45 at 960, 75 at 1920). No line comes closer than 80% of the gap to any other letter on the way
  in (the corner set may step down to 70% if nothing clears 80%). The four corner lines mirror each other
  left-to-right about the word's centre and top-to-bottom about the middle of the lowercase body: one curve, built
  for the bottom-left node and reflected to the other three, all four cut at the same length along the line (the
  shortest distance to a letter among them minus the gap, so each keeps at least the gap); their shared aim point
  is searched for the four distances to agree best. Live and Settings bow slightly. Everything is solved again on
  every resize (`solveLines`) and checked by `docs/design/mock/shot-resize.mjs` at 960, 1440 and 1920 px and after
  resizing either way (the corner ends fold onto one point within 1.5 px, equal lengths, the gaps).
- **Resizing (owner 2026-11-14):** the whole scene (wordmark size and position, particles, nodes, lines) is laid out again
  whenever the content area changes size (window resize, sidebar collapse). The wordmark's visible letters are centred
  horizontally, their vertical centre at 47% of the height; its size is `min(20% of the width, 300 px, 30% of the
  height)`. After the intro has played once, a re-layout shows the formed title at once.
- **Navigation in the scene:** one node per page (Live from step 21, Gameplan, Accounts, Settings, Review,
  Performance) placed around the title, linked to it by thin flowing lines. Each node: Lucide icon +
  page name, ≥ 44 × 44 px hit area, label `text-1` on a glass chip (contrast ≥ 4.5:1 over anything behind it).
  Hover / focus expands it into a small card (label, value, sub-line; 6 px from label to value and 7 px from value to sub-line, owner 2026-11-15) with its live number: Accounts = closest drawdown margin, Gameplan =
  next setup window, Review = cards left, Performance = this month's P&L (sign + arrow), Settings = risk per trade,
  Live = engine state. Click, Enter, keys `1`–`6` or Ctrl+K open the page.
- The top bar's status pill and FLAT ALL stay visible and work from the first frame.

## Opening sequence (Full)
1. The title forms alone (concept-specific, ≈ 1.2–1.8 s; order flow ≈ 2.5 s).
2. Once it settles, the nodes arrive one after another, clockwise from Live (Live, Review, Performance, Settings,
   Accounts, Gameplan; 200-ms stagger, each node fades and scales in over 350 ms); each node's line draws out from the
   node towards the word as the node arrives (600 ms, 80 ms after it). All six take ≈ 1.7 s. (Owner 2026-11-15.)
3. Idle: a mild loop (ripple / flow / glow) and cursor reaction; the flowing dashes on the lines fade in.
- Full intro on the **first open of a session** only (a session flag in `sessionStorage`, so a reload in the same tab
  counts too; if storage is blocked, the page load still remembers); later visits show the formed scene with a
  200-ms fade.
- Any click or key press during the intro (title or nodes) skips to the formed state at once. Sidebar, top bar and
  keys work from frame one. A node that hasn't arrived yet can't be clicked or tabbed to.
- Design preview only: `&intro=1` replays the full intro on every open of Home.
- Build note: the page-transition wrapper must not set `initial={false}` on `AnimatePresence` (it blocks the entrance
  animations of everything mounted later inside the first page shown); the first page is shown at once by its own
  wrapper's `initial={false}` instead.

## Calm
A still, fully formed wordmark with the nodes and lines in place; no idle loop, no cursor reaction.

## Concepts (three Playwright videos for the owner's pick)
1. **Cream pour:** cream liquid flows in and settles into the letters (21st.dev Liquid Text as the base, the forming
   intro added on top); ripples at rest; the cursor pulls beads off the strokes that sink back.
2. **Order flow (picked):** tick particles stream in from the edges like a tape and lock into the letters; a slow flow
   field moves through them at rest; the cursor pushes them aside; faint ticks keep crossing the scene. Brand colours
   only (owner 2026-11-14): gold from the left, lavender from the right, some cream; they settle to cream ("Cream") and
   gold ("Flow"). Green and red are never used here (reserved for P&L and status). Build base: 21st.dev Pixel Flow
   Field (30145); the mock is a prototype of the same technique.
3. **Price trace:** a glowing gold line draws the letterforms like a price trace, with a soft GEX-style heat glow
   pulsing behind.

## Budget
The scene is the page's only showpiece: ≤ 4 ms per frame, DPR ≤ 2, one rAF loop, stopped when the tab is hidden or the
page is left. No birds, no doodle characters (inspiration only).
