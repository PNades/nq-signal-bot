# UI polish, round 3 (owner 2026-11-24)

Built 2026-11-24 on the synthetic panel (`nqbot ui --synthetic`, port 8733), waiting for the owner's review. SPEC §13
Q-ui-polish-3. No font change: Geist Mono stays (owner, closing round 2's font question). Every change was
screenshotted before and after at 960, 1280 and 1440 px with a playwright script kept outside the repo (the session
scratchpad); the numbers below come from it and are the same at all three widths. Text positions are measured on the
letters themselves (the baseline from a zero-size probe, the ink from `measureText` in the element's own font).

## 1. Calendar stats: one baseline

- **Before:** each stat's row centred its items, so the smaller parts ("10 green · 9 red", "11 W · 20 L") sat 2 px
  above the main value's baseline, and the best / worst day's date 7.5 px above it: the signed amount's box took its
  baseline from the arrow (its first item).
- **After:** the row aligns on baselines; `Signed` aligns its amount on the baseline and centres the arrow on its own
  (`self-center`), so its box's baseline is the amount's. Every part: 0 px from the main value's baseline. The arrows'
  middle stays 0.17 px from the digits' middle (unchanged); the day and week boxes' arrows are unchanged.

## 2. The day panel

- **Spacing:** the clear space between "DAY" and the date and between the date and the "2 trades" line: 3 px and 20 px
  before, 11 px and 11 px after (the trade list starts 1 px higher; the X stays centred on the label and date).
- **Closing:** the X, a click anywhere outside the panel (the click on another day closes this one and opens that
  one), or Esc, like the info boxes; a click inside keeps it open (all four checked at every width; before, only the
  X closed it). Test: `tests/test_ui_polish_3.py`.

## 3. Gameplan's top boxes

- **Before:** Status and Flags to review 16 px padding with their content at the top (17 px of room above and below
  the content, measured from the border's outer edge); Next 16 px padding with its content centred lower (23 px);
  114 px high side by side; at 960 (one column) Next was 102 px, the others 114.
- **After:** all three 20 px above and below an 80-px content box: 21 px of room above and below in every box, 122 px
  high at every width. Next's label sits at the top and its note at the bottom of the same box, so the three labels
  and the three last lines line up across the row; its flip-disk clock is centred in it.

## 4. The footer's "cream"

- **Cause:** the weight was a function of the footer's width (`footerWeight`, owner 2026-11-15): 600 at the 960-px
  window, 700 at 1280, 750 at 1440, 900 only at full screen (1920). Not optical sizing (Geist Variable has no `opsz`
  axis) and not a stroke (the dots are fills sized by the letter's coverage).
- **After:** Black (900) at every width (`FOOTER_WEIGHT`), measured on the drawn word (`data-hwf-weight`). Only the size
  follows the width; the heavier letters are wider, so at the same footer width the word is set about 4% smaller
  (letter height 163 → 155 px at 960, 214 → 209 at 1280, 247 → 237 at 1440) and still fits inside the details' side
  padding. Test: `tests/test_ui_polish_3.py`.
