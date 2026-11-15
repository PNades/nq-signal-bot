# Review (SPEC §9b item 5, §8)

> Overrides MASTER.md for this page.

## Layout override
- Focused card view: the card's facts (zone, stack, entry style, entry / stop / target, detector readings, Skylit maps
  at that minute) in an "at this point" side panel; the **main chart** below, full width, following the replay clock
  (Restart / Play / Minute → / Next card, speed select, replay slider).
- **No lookahead:** the chart stops at the decision time until the owner answers (§8: the outcome stays hidden); after
  the answer the rest of the path reveals with the result.
- Answer bar: **Take** (`T`) / **Skip** (`S`) buttons, a reason box (required, 1–2 sentences, `Ctrl+Enter` submits),
  optional entry choice, stop/target and confidence 1–5; shortcut hints shown on the buttons.
- Batch progress (e.g. 18 / 25) and the running score on the **Flip-Disk Matrix** (snaps in Calm).
- After each answer a **result card** drops in with a slight spring bounce (Full): Take profit / Stop loss / Timeout,
  Correct / Incorrect against the outcome, net R (sign + arrow). Calm: appears at once.
- GEX snapshot radial for the card's minute, following the replay clock.

## Rules
- Focus stays in the answer bar between cards; the reason box keeps its draft if the owner navigates away.
- The skill's onboarding rule applies: Back and Skip are always available; nothing forces a linear flow.
