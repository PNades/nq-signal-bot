# UI polish, round 2 (owner 2026-11-24)

Built 2026-11-24 on the synthetic panel (`nqbot ui --synthetic`, port 8733), waiting for the owner's review. SPEC §13
Q-ui-polish-2. Every change was screenshotted before and after at 1280 and 1440 px (the week and day views also at
960) with playwright scripts kept outside the repo (the session scratchpad); the numbers below come from them.

## 1. Info icons

- **Before:** the ⓘ beside a chart title (`ChartHeader`, the only interactive info icon: Drawdown, Monte Carlo and
  their views) was a bare SVG with `tabIndex` and a `data-hint`: the global hint layer showed its text after 450 ms and
  hid it on any click; a mouse click focused the SVG and Chrome drew its own focus ring (`outline: auto`), which the
  keyboard-only rule (`[data-input="pointer"] :focus-visible`) doesn't reach.
- **After:** `components/ui/info-tip.tsx`, a real button. Hover opens the box; moving off both the icon and the box
  closes it (140 ms grace to cross the gap). A click pins it open: it stays without the pointer; a click outside the box,
  Esc, or a second click on the icon closes it. Tab onto it opens it, Enter / Space pins it. A mouse click leaves no
  outline (measured: focused element `BUTTON`, `outline: none`; box still open after the pointer moved away); the
  keyboard keeps the cream ring. Test: `tests/test_ui_info_icons.py` (no bare focusable `<Info>` icon anywhere).

## 2. The figures' font: not changed, alternatives shown

- **Today:** Geist Mono Variable (`--font-mono`, `@fontsource-variable/geist-mono`), app-wide: every `font-mono`
  element (about 200 in 38 files: the calendar's stats and day / week boxes, the Monte Carlo numbers, the KPI tiles,
  the Review panel's prices, the Gameplan times, the account switcher, the dock clock, the chart axes'
  `fontFamily`), every `.eyebrow` label and `.cf-num`.
- **Prepared, no visual change:** a `--font-num` token (still Geist Mono) and a `font-num` class (tabular figures) on
  the figures the owner named: the calendar's stats, day and week amounts, the day panel's times and prices, the Monte
  Carlo ring, hero, legend values and stat values. A pick changes one line in `styles.css` (plus the font package).
- **Alternatives** (each bundled locally from an npm `@fontsource` package, tabular figures checked: ten 1s and ten 0s
  equally wide): A Geist (the panel's own sans), B IBM Plex Mono, C JetBrains Mono, D Instrument Sans (pairs with the
  Instrument Serif headings). The side-by-side sheet: the same calendar stats, a week of day boxes and the Monte Carlo
  panels in each.

## 3. Spacing of the calendar's numbers

- The ▲ / ▼ beside an amount is centred on the figures (its middle within 0.4 px of the number's), not on the text
  baseline where it sat low (`Signed`).
- The month's stats: the main figure large, its parts smaller and muted beside it ("19 days  10 green · 9 red"); one
  line, never truncated (before: "19d · 10 green · 9…" and "Mar…" at 1280); five in a row with rules between them
  where the card is wide enough (a container query on the card), else three and two without rules (before, at 960, the
  second row's items were indented by the first row's rules).
- Day and week boxes: 96 px (from 92), 4 px between their lines, separators spaced by the row's gap.

## 4. The day panel (clicking a day; a week box has no click)

- **Before:** an eight-column table in a 460 px panel: "11:45strategyshort(c)" ran together, the entry → exit and the
  net wrapped and collided.
- **After:** each trade on two lines: time, direction (coloured) and style over the account, size and entry → exit; net
  and R on the right. Nothing overlaps at 960, 1280 and 1440 (the panel is the same width at all three). The week
  boxes themselves: no overlap or clipping at 960 / 1280 / 1440 (measured).

## 5. Monte Carlo pass-rate box

- **Before:** the legend was centred in the card's height, so without the extra line ("per path … · order luck …")
  the legend dropped 22 px, leaving a gap under the title.
- **After:** the legend sits 16 px under the title, the extra line 10 px under the legend when there is one; the legend
  doesn't move when the line is missing (measured with the line removed).

## 6. Gameplan

- Timeline: the axis is inset 20 px from the card's edges, so 08:00 (and 16:00) sit centred on their ticks like the
  other hours; the 16:15 end has a tick and no label (it collided with 16:00).
- Status tile: "No gameplan" 12 px under the label and the line under it 10 px below it (before 8 px and 4 px).

## 7. Chart zoom (Review's main chart)

- **Before (Lightweight Charts):** each wheel event zooms 10% × min(1, |Δy| / 100). Measured per gesture: a mouse notch
  ×1.101, a two-finger scroll (12 events of 4 px) ×1.049, a pinch (15 events of 2 px) ×1.031.
- **After (`chart/wheelZoom.ts`, the library's wheel zoom off):** each event zooms exp(0.13 · tanh(|Δy| · 0.0025 /
  0.13)): linear at 2.5× the library's slope for small deltas, capped for big ones. Measured: mouse notch ×1.133, the
  same two-finger scroll ×1.127 (2.6× the zoom), the pinch ×1.078 (2.5×). The bar under the pointer stays there
  (moved 0.00); one redraw a frame; horizontal scrolls still pan. After zooming far out and panning right, nothing past
  the replay position is drawn (no bar after the clock, no line or orb past the cut).
