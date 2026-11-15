# Step 14a part 0, step 4, round 2: floating dock and the dot-matrix footer (2026-11-15)

Your picks from the [step-4 report](step4_report.md): the floating dock as the nav, and four changes to the
dot-matrix footer. Built in the mock (`docs/design/mock/`); nothing is committed. The screenshots are in
[`step4/round2/`](step4/round2/) and the measurements in
[`step4/round2/measurements.json`](step4/round2/measurements.json), both from `docs/design/mock/shot-round2.mjs`. That
run had no blocked requests and no console errors or warnings.

## The floating dock

- **Default nav** in the preview; `&nav=sidebar` and `&nav=waterline` still show the other two for comparison.
- **Not shown on Home** ([home_no_dock_1440](step4/round2/home_no_dock_1440.png)); on every other page it shows as
  before.
- **Minimize circle** at the end of the dock, behind a hairline ([dock_open_1440](step4/round2/dock_open_1440.png)).
  Clicking it collapses the dock into one 44-px glass circle, and clicking the circle expands it again. One glass
  shell morphs between the bar and the circle with a short spring while the contents fade, so it animates both ways
  (instant in Calm). The state is remembered for the session: across pages, and after a reload in the same tab
  (checked).
- **Two variants for the minimized circle**, preview switch `&dockmin=`:
  - `&dockmin=centre` (the default now): the circle stays at the bottom centre:
    [dock_min_centre_1440](step4/round2/dock_min_centre_1440.png), scrolled to the bottom
    [dock_min_centre_bottom_1440](step4/round2/dock_min_centre_bottom_1440.png).
  - `&dockmin=corner`: the circle moves to the bottom-right corner:
    [dock_min_corner_1440](step4/round2/dock_min_corner_1440.png), scrolled to the bottom
    [dock_min_corner_bottom_1440](step4/round2/dock_min_corner_bottom_1440.png).
  - Video of both, minimizing and expanding (≈ 14 s, gitignored):
    [step4_dock_minimize.webm](video/step4_dock_minimize.webm).
- **Bottom padding:** every page gets the dock's footprint plus 16 px of air (96 px with the dock open, 76 px
  minimized). On the style tile scrolled to the bottom, the last card ends at 556 px and the dock starts at 618 px
  ([tile_bottom_dock_1440](step4/round2/tile_bottom_dock_1440.png)).
  - **My reading of items 3 and 5 together:** the Performance page ends in the footer, so there the padding is
    cancelled and the page ends inside the letters. The dock then floats over the dot letters (decoration, not
    content); the footer's details line stays clear of it at every width. If you'd rather the dock never cover the
    letters, the alternative is a band of empty space below them, which item 5 rules out.

## The dot-matrix footer

| Width | Weight | Footer height | Ink margin left / right | Details → letter tops | Page ends inside the letters |
|---|---|---|---|---|---|
| 1920 | 900 (Black) | 425 px | 48 / 49 px | 61 px | yes, 0 px below |
| 1440 | 750 | 354 px | 43 / 48 px | 48 px | yes |
| 1280 | 700 | 324 px | 43 / 44 px | 37 px | yes |
| 1024 | 620 | 277 px | 36 / 35 px | 29 px | yes |
| 960 | 600 (SemiBold) | 265 px | 32 / 36 px | 31 px | yes |

- **Word:** "cream", lowercase, at every width, in Geist (the variable font already bundled). Weight follows the
  footer's width in a straight line: 600 at the 960-px window, 900 at 1920 px (`footerWeight` in
  `src/perf/PerformancePage.tsx`).
- **End of the page:** scrolled all the way down, the page ends inside the letters. Their lower quarter is cut off
  by the bottom edge, and there are 0 px below the footer at every width (measured).
- **Size:** the letters' ink fits the footer's width minus the details' side padding (33–44 px), so it lines up
  with "CREAMFLOW · NQ" and never runs off. The footer's height follows from the word: details, the hairline, a
  clear gap of max(32 px, 18% of the letter height), then the letters.
- **Sizing cause (your item 7):** the original component sized the word so its advance width filled 102% of the
  footer, by design. That pushes the ink past both edges, more so when the font's side bearings are small, so it
  only looked right at some widths. It now fits the ink box. It measures the footer's own box (never the viewport)
  and again on every resize, and asks for the weight in use explicitly before measuring, so a late font never leaves
  the fallback face in place.
- **Screenshots, scrolled to the bottom:** [1920](step4/round2/footer_1920.png),
  [1440](step4/round2/footer_1440.png), [1280](step4/round2/footer_1280.png), [1024](step4/round2/footer_1024.png),
  [960](step4/round2/footer_960.png).
- **After resizing:** [1920 → 960](step4/round2/footer_resize_1920_to_960.png) and
  [960 → 1920](step4/round2/footer_resize_960_to_1920.png). Each resized page measures exactly the same as a fresh
  load at that width.

## Fixes found on the way

- **Details line wrap:** at 960 px the footer's details line wrapped ("LOCAL CONTROL PANEL · / 127.0.0.1"), because
  the empty link columns still took three quarters of the width. With no link columns, the details now use the full
  width.
- **Chart range after a resize:** after a resize from 1920 to 960 the equity chart kept its bar spacing and showed
  only January to March. The equity and drawdown charts now refit their whole range when their box changes size.

## Docs updated

`design-system/pages/shell.md` (the dock replaces the sidebar), `design-system/pages/performance.md` (the footer),
`design-system/MASTER.md` (the hero type row), and SPEC §13 Q-step14a (11) with the decisions only. The fast docs
tests passed.

## What I need you to pick

1. **Minimized dock:** `corner` or `centre`.
2. **Dock over the letters:** OK that the dock floats over the footer letters at the bottom of the Performance page?
3. **Then:** the showpieces to keep and any Performance changes from the [step-4 report](step4_report.md), if not
   settled yet.
