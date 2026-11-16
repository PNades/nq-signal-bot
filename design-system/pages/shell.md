# App shell and shared elements

> Overrides MASTER.md only where stated. Applies on every page. Draft for the owner's picks (step 14a part 0).

## Layout
- **Navigation = the floating dock** (owner's pick 2026-11-15, over the sidebar and the Waterline bar; 21st.dev 18014):
  a glass bar at the bottom centre, 16 px above the edge, 64 px high, one round icon per page in sidebar order (Home,
  Live from step 21, Gameplan, Accounts, Settings, Review, Performance) with a tooltip on hover, magnification as the
  pointer passes (none in Calm), the current page marked by a gold wash and a cream dot.
  Magnification up to 1.2× only (owner 2026-11-15). The unsaved-changes bar follows the dock: just above it, clear of
  its hover labels, while it is open; near the bottom edge while it is minimized; a 220-ms slide, instant in Calm.
  Toasts keep clear of the dock and of that bar.
  - **Not on Home:** Home's six nodes, keys `1`–`6` and `Ctrl K` cover navigation there.
  - **Minimize circle** at the end of the bar, behind a hairline: it collapses the dock into one 44-px glass circle,
    and clicking the circle expands it again (one shell morphs between bar and circle, a short spring; contents fade;
    instant in Calm). The state is remembered for the session (`sessionStorage`, a reload in the same tab keeps it).
    The circle moves to the **bottom-right corner** (owner's pick 2026-11-15; the other variant, staying at the bottom
    centre, stays in the preview as `&dockmin=centre`).
  - **Dark glass:** the dock's shell is 90% `#111111` with a backdrop blur (owner 2026-11-15: it may float over the
    footer's cream letters, but its icons must stay clearly readable there); the icons sit on opaque `bg-3` circles.
  - **Bottom space:** every page gets bottom padding equal to the dock's footprint plus 16 px of air (96 px open, 76 px
    minimized), so no content stays behind the dock when scrolled to the bottom. A page that ends in the dot-matrix
    footer cancels it: that page ends inside the letters, which the dock may float over.
  - **Toasts never under the dock** (owner 2026-11-15): they sit in the bottom-right corner, their bottom edge at the
    dock's footprint plus 16 px (96 px open, 76 px minimized), whichever variant; one CSS variable (`--toast-bottom`)
    set by the shell; 14 px from the edge on Home (no dock).
  - The top bar shows the logo mark + CreamFlow wordmark at its left (there is no sidebar).
- The sidebar (248 px, collapsible) and the Waterline bar stay in the design workspace only for comparison
  (`&nav=sidebar`, `&nav=waterline`).
- **Top bar** (`bg-1`, 52 px): breadcrumb eyebrow `CREAMFLOW / <PAGE>`, account switcher (name + a small phase badge,
  e.g. `EVAL`; a dropdown listing every account with its room to the floor, today's P&L, progress and lives, owner
  2026-11-15; picking one sets the panel's focus account, never which accounts trade), engine status pill, ET clock (mono, recomputed every second from the system clock, stale cue if the
  clock check fails), Effects toggle (Full / Calm, shows "Calm (auto)" when forced), the `Ctrl K` hint (Windows: never
  `⌘`), and on the far right the **FLAT ALL** control (MASTER §10). Nothing ever covers the top bar's right end; dialogs
  open below it in z-order except the kill confirm.
- **Nothing in the top bar wraps (owner 2026-11-14).** FLAT ALL is always one line, fully visible and readable. As the
  window narrows, lower-priority items go first: the breadcrumb (below 1,180 px), then the Ctrl K hint (below 1,040 px),
  then the Effects label (below 1,100 px; its icon stays). The account switcher, status pill, clock and FLAT ALL always
  stay. Checked automatically in every screenshot run (FLAT ALL one line high, nothing overflowing).
- **Screenshot widths (owner 2026-11-14):** every page is shot at 1440, 1280 and 960 px.
- **Ticker tape** (28 px, under the top bar): the day's key levels and their state, marquee in Full, a static
  scrollable row in Calm; paused on hover.
- **Critical banner** slot under the ticker: crit alerts stay until acknowledged, stacked, newest first.
- **Banners (owner 2026-11-15):** info banners slide down with a short fade (220 ms) and fade out after ~5 s, the
  countdown paused while the pointer is over them, "Got it" closes at once; warnings and critical alerts never fade by
  themselves; critical ones appear at once without motion (MASTER §7). Calm: no motion.
- **Bell (owner 2026-11-15):** in the top bar before KILL; the history of every banner and toast shown in this tab
  (sessionStorage, last 100), tabs All / Alerts / Info, unread badge (crit-red while a critical one is unread); its
  panel opens below the banner stack, never over a banner.
- **Date block (owner 2026-11-15):** the serif day number with "NOV 2026 / SUN · EASTERN", New York's date, fixed in the
  bottom-left corner on every page (Home too) on a dark chip like the dock; pages keep at least 76 px bottom room;
  hidden below 900 px (the top bar's clock stays). Home's scene (stage 2) keeps that corner clear.
- **Page header:** eyebrow, two-line title (second line cream italic), one-line subtitle; then KPI row, tabs, body.

## z-index scale
`base 0` · `sticky 10` (table headers) · `sidebar 20` · `topbar 30` · `popover 40` · `dialog 50` · `toast 60` ·
`critical 70` (critical banner, kill confirm). No ad-hoc z-index values.

## Keyboard
`1`–`6` open pages in sidebar order (Home 1, Live 2 from step 21, Gameplan, Accounts, Settings, Review, Performance
renumber when Live exists: shown in the hints), `Ctrl+K` command palette (fuzzy: pages, accounts, actions; the
kill/flat actions are listed but still ask their normal confirm), `F` full-screen chart, `Esc` leave / close,
`?` shortcut sheet. A skip-to-content link is the first focusable element. Shortcuts never fire while typing in a field.

## Shared states
- **Loading:** skeletons shaped like the content (no layout shift); the splash/loading screen only on first load.
- **Empty:** the dot-matrix wordmark or Dot Matrix Text with a one-line message and the next action; never urgent info.
- **Error:** inline panel with what failed, the time, and a Retry; data errors mark the affected numbers stale.
- **Stale:** `—` or the last value dimmed to `text-3` with a `Clock` icon and "as of HH:MM:SS".

## Page transitions
Owner 2026-11-16 (SPEC §13 Q-pages; replaces the fade-out-then-rise): the current page stays until the next page's data
is loaded (`lib/nav.ts` preloads it, also on hovering a dock icon, a Home node or a link marked `data-preload`), then the
two crossfade (240 ms, both on screen, never a blank gap) and the next page arrives filled: no placeholders, no
roll-up or draw-in on arrival. The scrolling pages share one footer (`PagesFrame`), whose position glides with the page
height (the tab switch's 250-ms glide, frame-driven). A change slower than ~300 ms shows a thin progress line under the
top bar (`BusyBar`). Calm: an instant swap. The top bar, dock and kill/flat control never take part in a transition.

## Dot-matrix footer
The halftone CreamFlow wordmark spans the full width at the bottom of the scrolling pages (Performance, Accounts,
Settings), the lower quarter of the letters cut off by the page's bottom edge even when scrolled to the end; dots near
the cursor part and tint ember. Off in Calm (a still dot render instead); never in the sidebar.
