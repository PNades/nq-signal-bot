# UI polish, round 1 (owner 2026-11-18)

Built 2026-11-18 on the synthetic panel (`nqbot ui --synthetic`, port 8735), waiting for the owner's review. SPEC §13
Q-ui-polish-1. Screenshots and measurements: `docs/design/build/stageu1/` (`node ui/web/scripts/shoot.mjs u1` with
`CF_TAG=before|after`; `checks_after.json`; the before numbers below were measured by the same stage before the change,
whose file the after run overwrote before the stage kept one file per tag).

## 1. Text-field focus

- **Before:** the field's border turned cream at 70% *and* the app-wide `:focus-visible` ring (2 px cream, 2 px off)
  appeared as soon as a key was pressed (the ring is keyboard-only, and typing counts as the keyboard). Outline
  `solid 2px` in all four runs (mouse / keyboard × 1440 / 1280); focused border vs unfocused border 2.2:1 (below 3:1).
- **After:** no outline on text and number fields, for the mouse or the keyboard (`styles.css`, unlayered:
  `input` except range / checkbox / radio / file / button / submit, and `textarea`). The field's own border turns full
  cream: rgb(255,224,194) vs the unfocused rgb(72,72,72) **7.27:1**, vs the page 14.1:1. Buttons, switches, tabs,
  sliders and checkboxes keep their keyboard-only ring.
- Every text field in the app now lights its border the same way: the form controls (`TextInput`, `NumberInput`),
  Review note and decimal fields, Performance trade search and date range, Gameplan note and inline edits (these had a
  permanent cream/60 border, now the normal border until focused), and the search rows in the account switcher, the
  Performance menus, the preset picker and the shortcuts sheet (their divider line is the field border). The command
  palette keeps its borderless search (owner's earlier choice).
- Close-ups: `focus_mouse|keys_1280|1440_before|after.png`.

## 2. "·" separators

- **Unsaved-changes bar:** before, the dot was inside the note's text (12.5 px of space on its left from the bar's
  flex gap, 4 px on its right from a space character). Now the label, the dot and the note are three elements in
  their own group with a 6 px gap: **6.5 / 6.5 px** from the visible text edges. Close-ups
  `unsaved_bar_1280|1440_before|after.png`.
- **Audit:** every "·" on Home, Accounts, the account editor (with the bar open), Settings, Performance, Review,
  Gameplan (top and bottom of each page) and the notifications panel, at 1440: 35 measured, all within **0.5 px**
  left vs right after the change (before: the bar 8.5 px off; the synthetic strip 1.5 px, from the backticks around
  `nqbot ui --synthetic`, whose glyph sits off-centre in its mono cell: the backticks were removed).
- **Notifications:** a long title wrapped just before a " · ", so the dot started a line (measured 21.5 / 4.5 px).
  `notify()` now joins each " · " to the word before it (a no-break space), so a dot never starts a line
  (`dotsAtLineStart` 0; `bell_1440_after.png`). This check was added after the change, so it has no before picture.

## 3. Breadcrumb

- Measured on the letters' bodies (lowercase from the x-height, capitals from the cap height, to the baseline; the
  ink box alone is misleading because the wordmark's C, F and l rise above its x-height).
- **Before:** the crumb's middle 1.75 px above the wordmark's, 0.5 px above the account name's; 16 px from the
  wordmark. The same on every page (/accounts, /settings, /performance, /review, /gameplan, /accounts/mffur1) at
  1280 and 1440.
- **After:** moved 1 px down and 1 px right (`relative top-px left-px`, nothing else moves): 0.75 px above the
  wordmark's middle and 0.5 px below the account name's, so between the two; 17 px from the wordmark; the same on
  every page at both widths. Close-ups `crumb__accounts|gameplan_1280|1440_before|after.png` (3×).

## Tests

Targeted (rule 14 (b)): `tests/test_ui_server.py`, `tests/test_ui_build.py` and the fast docs/config tests. Not pushed.

## Open

- The owner's review.
