# Build stage 3: Performance (SPEC §9b item 6)

**Evidence:**
- Screenshots and checks against `nqbot ui --synthetic`: [`build/stage3/`](build/stage3/)
  ([`checks.json`](build/stage3/checks.json)).
- Against the plain `nqbot ui` (the empty state): [`build/stage3e/`](build/stage3e/).
- Both runs passed all checks; stages 2c and 2e re-ran clean (2e also checks that no page carries a native tooltip or
  scrolls sideways, Performance included).

## What the page reads
- **The trade ledger** (`src/nqbot/performance/ledger.py`). This stage fixes the format that steps 15 and 22 will write:
  - one JSON line per closed trade, with its New York trading date, entry and exit times, direction, entry style, size
    (minis / micros), entry and exit, net $ (after commissions), R, commission, and the worst open loss (`mae_usd`, for
    the intraday drawdown);
  - backtest runs: `<data_dir>/backtest/runs/<run>/trades.jsonl` (step 15);
  - the live journal: `<live_dir>/journal/trades_<session>.jsonl` (step 22; demo from step 24, prop from step 26).
  - Nothing exists yet, so each source says so: "no backtest trades yet (from step 15)", "no demo trades yet (from step
    24)", "no prop trades yet (from step 26)".
- **The synthetic ledger** (`synthetic.py`; `nqbot ui --synthetic` only, never the default):
  - seeded and repeatable, spread over all twelve ledger months plus 15 demo sessions after the window, on the panel's
    own accounts with their own risk and commissions;
  - the yellow SYNTHETIC DATA strip and a SYNTHETIC DATA chip on the page;
  - with the default accounts, the seed (96) gives no floor reached in the backtest order and both funded floors
    locking.

## The ledger guard (§7 rule 2b; `performance/guard.py`, the required unit test `tests/test_ledger_guard.py`)
- **Development months 1–6:** always shown.
- **Walk-forward months 7–9:** totals and gate metrics only, once gate 16 is recorded in `config/gate_record.json`.
  Never trade by trade or day by day.
- **Sealed months 10–12 and every month after the window:** totals only, once gate 20 is recorded. Never trade by trade
  or day by day until §13 Q-ledger-after20 is decided.
- **Live, demo and prop trades:** always shown.
- **On the page:**
  - What is held back is listed under the header, with the count, the reason and the missing gate. Today: "Sealed: 61
    trades not shown … gate 20 not recorded" and "Walk-forward 7–9: 64 trades not shown … gate 16 not recorded".
  - Picking Walk-forward 7–9 or Sealed in the phase menu is refused (403) with the reason, and offers "All phases".
  - The calendar says so on a month whose backtest days are held back.
- **Everywhere:** the API applies the guard on every route (page, trades, calendar, day, Monte Carlo) and on the CSV
  export. An export asking for a held phase is refused outright; "everything" contains only what may be shown trade by
  trade, and the export's toast says how many trades were held back.
- **Tests:**
  - months 7–9 before gate 16 refused;
  - sealed months before gate 20 refused;
  - months 7–9 and sealed months at trade or day level refused with any gates, CSV export included;
  - a missing or unreadable gate record counts as no gate passed;
  - the committed record has no gate 16 or 20 yet.

## The page
- **Header:**
  - the account filter, which follows the top-bar focus account;
  - the phase menu;
  - a date range (from / to, or all dates);
  - `$ | R`;
  - Export CSV.
- **KPI tiles** (approved order): Net P&L, Trades, Net R per trade, Win rate, Profit factor, Max drawdown. Each has a
  monthly sparkline; its delta compares the last month with the earlier months.
- **Stats panel** (§9b; added under the tiles):
  - wins / losses;
  - average and largest win and loss;
  - max drawdown close to close and intraday (from each trade's worst open loss);
  - longest and average streaks.
  - Totals per phase sit beside it, e.g. "Demo: 14 trades …".
- **Equity:**
  - one time axis, each phase in its own colour with a marker where it starts (development gold, demo blue);
  - a crosshair tooltip with equity, the day, trades and the drawdown;
  - **Live vs expected** (§9b) is a toggle inside the Equity panel ("Equity curve | Live vs expected"): the demo / live
    R curve over the 5–95% band from 1,000 draws of whole development days to the same trade count, with "inside" or
    "outside the band".
  - It's a toggle rather than a sixth tab so the page keeps the approved five tabs, and the account tabs still fit at
    1440 px (a sixth tab pushed them into the dropdown).
- **Drawdown:**
  - per account under its own §6.7 rule (`performance/floor.py`, the five hand cases in `tests/test_perf_floor.py`);
  - the floor as a stepped dashed line, the closest approach as a dot, with its value and date in the header;
  - "floor locked" / "floor reached" badges.
- **Monte Carlo:**
  - per account, 1,000 paths of whole days through the account's own floor rule;
  - the chance of reaching the floor and the median trading day it happens;
  - the 5–95% and 25–75% bands, the median, the smallest and largest drawdown paths, the backtest order and its floor;
  - **open question below.**
- **Calendar** (§9b exactly):
  - Mon–Fri plus Week;
  - month picker and previous / next;
  - day boxes with net, trades and W / L: green or red, blank when there are no trades, uncoloured at exactly $0;
  - the Week column with P&L, days traded, trades, W / L and win rate;
  - the header with month P&L, trading days (green / red), trades (W / L), and the best and worst day with dates;
  - `$ | R`, where combined R = the sum of the accounts' R;
  - clicking a day opens its trades (time, account, direction, style, size, entry → exit, net $ and R).
- **Trades:** sortable, filterable, sticky header, a column chooser, rows windowed past 200.
- **Account tabs:** Drawdown and Monte Carlo show the account tabs centred in the tab row at 1440 px (checked:
  `data-fit=tabs`), the compact dropdown at 960 px.
- **CSP:** Lightweight Charts appends one fixed `<style>` for its attribution logo, which its licence requires. It is
  allowed by the SHA-256 of exactly that text (`security.LWC_ATTRIBUTION_CSS`), still with no `unsafe-inline`. The
  Border Beam Panel's inline `<style>` moved into `styles.css`.

## Open question for the owner (SPEC §13 Q-perf-mc)
§9b says the Monte Carlo makes "1,000 reshuffles by day blocks". That can mean:
- **(a) Bootstrap:** whole days drawn with replacement. This is what the approved mock did, and what the spec means by
  "day-block bootstrap" everywhere else. The fan widens towards the end, because each path's total differs.
- **(b) Permutation:** the same days in a new order. Every path ends at the same total; only the order, and so the
  drawdown and the chance of reaching the floor, changes.

The code does both. The page uses (a) until you decide.

## Stage 3 changes (owner 2026-11-15)
- **Evidence:**
  - Screenshots and checks: [`build/stage3b/`](build/stage3b/) ([`checks.json`](build/stage3b/checks.json)).
  - Videos: [`video/stage3b/`](video/stage3b/), `footer_check_1440.webm` and `montecarlo_drawout_1440.webm`.
  - All checks passed on the synthetic ledger; stage 3 re-ran clean.
- **Approved:** the stats panel, the live-vs-expected toggle, the ledger format, and the chart attribution stylesheet
  allowed by its fingerprint.

1. **KPI:** in $, the tile reads "Net $ per trade" with the value and the monthly sparkline in dollars; in R, "Net R
   per trade" as before.
2. **Monte Carlo and Drawdown: two modes, chosen by the phase.** Both draw days from the strategy's guarded backtest
   ledger (the development days; walk-forward and sealed never), never from one account's own trades.
   - **Backtest / simulator phases (All, Development …): by account type.**
     - Every preset is a type: the ten §6.7 stages plus your saved presets.
     - Each type turns the same days into its own dollars:
       - its risk, capped at drawdown ÷ N;
       - its commissions on each trade's contracts;
       - its daily caps: the consistency profit cap, and the bot's daily loss cap or else the firm's;
       - its drawdown type, size and lock.
     - An in-tab menu picks the type (← → step through it). On the Monte Carlo, a comparison strip above the fan has
       one row per type: risk, the chance of reaching the floor, the median day, target first (evaluations), the
       median result at the end, and order luck. Click a row to pick it.
   - **Real-trades phases (Demo, Prop, Live): one actual account.** It's the one in the page's account filter; with All
     accounts, the focused one, with the note "showing mffur1 · change it in the account menu".
     - **Drawdown:** its own real trades.
     - **Monte Carlo, "from today":** paths start at its current balance, high water and floor in force.
     - That state comes from the engine's `<live_dir>/account_state.json` (step 22) when it exists, else from its
       trades in the ledger.
     - Before step 22 there is no live balance, so the page says so. Under `--synthetic` the stand-in is synthetic
       prop trades on your accounts after the window, labelled "SYNTHETIC" in amber.
   - **Method (Q-perf-mc decided):** (a), whole days drawn with replacement, is the headline (the fan, the chance of
     reaching the floor, the median day). (b), the same days reordered, is the "order luck" line under it and a column
     in the strip.
   - **Where a path stops (my assumption, SPEC §13 Q-perf-eval, for you to confirm):**
     - A path stops where the account's run ends: at the floor, or for an evaluation with a profit target, at the
       target (the evaluation passes). Its balance stays there.
     - So the fan's last column is the spread of results, and "median result" is its median.
     - Without the stop, every evaluation showed about a 99.8% chance of reaching the floor, because the paths ran on
       past the pass.
     - An account already past its target has no target from here.
     - The backtest-order line stops the same way.
   - **Speed:** the Monte Carlo is now vectorised (numpy): all ten types × both methods × 1,000 paths in 0.3 s (cached
     after). A test checks the vectorised floor against the scalar §6.7 rule on random paths for every lock type.
3. **Drawdown:** by account type for backtest phases (the same menu); the one actual account for real trades.
4. **The ledger guard** applies everywhere. Tested: the type mode's days are development days only.
5. **No footer flash.**
   - **The cause:** while Performance loaded, its short skeleton sat inside the page wrapper, so the "cream" footer was
     in view until the content pushed it down.
   - **The fix:** `FooterPage` takes `ready`. Until the content is in place, the content area is at least the window's
     height and the footer isn't rendered; then it fades in (240 ms, Calm at once). Accounts and Settings already
     returned their skeletons without the footer.
   - **Measured** after every painted frame from Home to Performance, Accounts and Settings (keys 6, 3, 4), at 1440
     and 960 px: **0 frames with the footer in view before the content.**
     - On Performance and Settings the footer is never in view during the switch.
     - On Accounts it comes in the same frame as the cards, because the page is short.
6. **Draw-out:**
   - When the Monte Carlo opens, or the type or account changes, the bands, median, backtest-order line, smallest /
     largest paths and floor draw out from the start to the end in 1.25 s ease-out (a clip-path reveal).
   - A click on the chart or any key skips to the end.
   - The numbers (chance, median day, strip) are there at once. Checked: the chance read 28.0% while the fan was
     still drawing.
   - Calm draws it whole (checked).
7. **Bug fixed:** switching between Monte Carlo and Drawdown drew the other tab's data for a moment and crashed. The
   tab now remounts per tab and mode, and never draws a response of the wrong shape.

## The strategy view by plan and lifecycle (owner 2026-11-15)
- **Evidence:**
  - Screenshots and checks: [`build/stage3c/`](build/stage3c/) ([`checks.json`](build/stage3c/checks.json)). Each plan's
    Monte Carlo and Drawdown at 1440 px; Rapid and Pro at 960 px; plus the plan menu, Compare plans at 120 days and My
    account.
  - The draw-out: `video/stage3c/plans_drawout_1440.webm`.
  - All checks passed.

1. **Plans, not stages.**
   - The menu lists plans: MFFU Rapid EOD 25k, MFFU Builder 25k, MFFU Pro 50k, Lucid Flex 25k, plus saved presets
     grouped by their name (none are on file now).
   - Each plan's evaluation and funded stages are used; "after first payout" isn't, since payouts aren't modelled.
   - The menu shows 5 at a time and scrolls (`maxVisible`).
   - The tab's title says which mode is shown: "Strategy · backtest on <plan>", with "the top-bar account menu applies
     to real trades only", or "My account · <name>".
2. **Monte Carlo (strategy), one plan** (`performance/plans.py`):
   - **Lifecycle:** each of the 1,000 paths starts as the plan's evaluation. On the profit target a fresh funded account
     starts (its start balance, floor, risk and caps). A floor ends the path.
   - **Plotted:** the strategy's cumulative P&L in the plan's dollars, continuous through the pass. The backtest-order
     line has a "passed" marker; a dashed tick marks the median day to pass; the evaluation target is drawn.
   - **X-axis:** trading days from the start: 30 / 60 / 120 (default 60), no calendar months.
   - **Headline:**
     - pass rate;
     - median days to pass;
     - the chance the funded account survives to the last day (of all paths, and of the passes);
     - the median result at the end: the funded account's P&L on the last day, 0 for a path that never passes.
   - **Also:** the order-luck line (the same days reordered) and "payouts not modelled".
   - **Compare plans:** opens the table, one row per plan with those numbers. Off by default.
3. **Drawdown (strategy):** the backtest days in order through the plan's lifecycle.
   - A pass is marked "✓ funded".
   - A floor is marked "✕ eval n": failed, and a new evaluation (number n) starts here. A legend line spells it out;
     the labels are short and numbered because long ones overlapped when failures came close.
   - The summary counts the evaluations used, passed, failed in evaluation, funded accounts lost, and where it ends.
   - E.g. synthetic Pro 50k: 6 evaluations, 5 passed, 5 funded accounts lost. Its funded floor trails until a payout,
     and payouts aren't modelled.
4. **My account (real trades):** unchanged: the account from the account filter or focus, its drawdown, "from today".
5. **Ledger guard:** tested; the plan views use development days only.
- **Synthetic numbers at 60 days:**

  | Plan | Pass | Median days to pass | Funded survives | Median result |
  |---|---|---|---|---|
  | MFFU Rapid | 72.5% | 4 | 53.5% | +$9,650 |
  | MFFU Builder | 70.7% | 4 | 51.4% | +$8,166 |
  | MFFU Pro 50k | 73.9% | 4 | 3.1% | +$2,331 |
  | Lucid Flex | 72.6% | 3 | 53.0% | +$9,337 |

  Synthetic data, not results.

## The strategy view, rework 3: plans, payouts, markers, design pass (owner 2026-11-15)
- **Evidence:** [`build/stage3d/`](build/stage3d/) ([`checks.json`](build/stage3d/checks.json), all checks passed): each
  plan's Monte Carlo and Drawdown at 1440 and 960 px, the plan menu, the stats box and Monte Carlo summary in variants A
  and B (`?variant=b`), marker tooltips at both edges and on a payout, Compare plans at 120 days, the calendar by plan,
  the real-trades account sync. Video: `video/stage3d/strategy_drawout_1440.webm`.
1. **Plan selector.** In Development (and walk-forward / sealed) the header's account filter becomes the plan menu
   (5 rows visible, then it scrolls; ← → step). Every number follows the plan's dollars: KPIs, stats, Equity, Drawdown,
   Monte Carlo, Calendar, Trades, the CSV (R unchanged). The synthetic backtest is one strategy ledger (account
   `strategy`, $333 a trade, repriced by plan: Pro 50k doubles every dollar). Demo / prop / live: the account filter
   (no "All accounts") and the top-bar switcher are one selection, both ways (checked). All phases shows both menus.
2. **Lifecycle with payouts** (`src/nqbot/performance/lifecycle.py`, one vectorised engine for the page and the step
   15-16 simulator, 16 hand-case tests): evaluation (fee) → target with the minimum trading days → funded (activation
   fee) → payouts at the maximum whenever the rules allow (buffer, minimum / maximum, % of profit, days per cycle,
   profit days, calendar clock, profit since the last, consistency, cap group; the first-payout floor lock) → a floor
   fails the account and a new evaluation starts → until 30 / 60 / 120 days.
3. **Monte Carlo:** evaluations used, pass rate, days to pass, payouts taken, payout total, fees, net cash: medians with
   10-90% ranges; the fan is the balance; the backtest-order line with its markers and its stage lines. **Drawdown:**
   the replay with each stage's own floor, the target only in an evaluation, the payout line only while funded, totals.
4. **Markers:** small dots (✓ passed, ◆ payout, ✕ failed) with a tooltip on hover or focus, kept 12 px inside the plot,
   the tooltip clamped inside the chart (checked on the first and last markers of every plan at both widths).
5. **Design pass:** chart headers (eyebrow + title + controls, rules in an info hint), short legends, the summary and
   the stats box in two variants each.
- **Fees:** no preset states an evaluation or activation fee, so fees and net cash show "fees missing" (listed, never
  guessed) and the hero shows the payouts to you instead.
- **Synthetic numbers at 60 days (medians):**

  | Plan | Evaluations | Pass rate | Days to pass | Payouts | Payouts to you |
  |---|---|---|---|---|---|
  | MFFU Rapid 25k | 4 | 69.8% | 5 | 11 | $10,997 |
  | MFFU Builder 25k | 2 | 71.0% | 4 | 5 | $5,000 |
  | MFFU Pro 50k | 3 | 73.0% | 4 | 4 | $18,377 (80%) |
  | Lucid Flex 25k | 1 | 72.7% | 3 | 5 | $4,500 (90%) |

  Synthetic data, not results.

## Rework 4: one layout in every phase, the account's own stage, zoom (owner 2026-11-16)
- **Evidence:** [`build/stage3f/`](build/stage3f/) (main panel, synthetic) and [`build/stage3g/`](build/stage3g/) (a
  scratch panel on 127.0.0.1:8734 with its own data root in the scratchpad: the two draft evaluations plus a funded
  Lucid Flex 25k demo account, since the owner's panel has evaluation drafts only); `checks.json` in each, all passed.
  Videos: `video/stage3f/phase_switch_1440.webm`, `video/stage3f/zoom_1440.webm`.
1. **Phase switching:** the KPI tiles (one height, 192 px, badge or not), the stats box, the header menus (fixed
   widths: phase 196 px, plan / account 212 px in one slot that crossfades), the scope chips and every chart header
   (64 px, the kicker truncated) are the same in every phase: measured equal in Development and Prop on Equity,
   Drawdown and Monte Carlo at 1440 and 960 px. The content crossfades in 200 ms when the new data arrives (the old
   view stays until then); numbers glide (the KPI tiles' roll, the stats box through the number ticker). Frame check:
   Full 4+ distinct Net P&L values and 3+ frames holding both views; Calm (reduced motion) one jump, no crossfade.
2. **Synthetic accounts follow their rules** (`synthetic.fit_rules`, `tests/test_perf_synthetic.py`): a session whose
   end of day would reach an evaluation's target or the floor in force is left out for that account. mffur1 is now an
   evaluation at $25,576 (target $26,500, floor $24,576).
3. **"From today" = the account's own stage** (`performance/account_mc.py`, `tests/test_perf_account_mc.py`; the same
   lifecycle engine with `start=` and `restart=False`): evaluation: pass before failing, the days to each; funded: the
   next payout before the floor, the days to it, payouts over the horizon. The old "P(reaching the floor from here)" is
   gone. The account's Drawdown shows its own last N days with its floor and its stage's line.
4. **Labels:** "floor (backtest order)" in the Monte Carlo legends and on the floor's own label; tooltips on it and on
   "backtest order".
5. **Horizon and zoom:** 30 / 60 / 120 days on all four charts (the replay = the first N backtest days; the account's
   Drawdown = its last N days); wheel zoom, drag to pan, double-click to reset; markers and the floor label stay inside
   the plot at every zoom (checked zoomed and panned).
7. **KPI sparklines per trading day** (`view.kpi_trend`), the badge "last 5 trading days vs earlier"; one trading day:
   a single dot, no badge.
8. **Profit factor ∞** with "no losing trades yet" and a tooltip (gross profit ÷ gross loss).
9. **No "All phases";** Development by default.
10. **Headline numbers** as small stats (label above, value below), the summary A layout for the account too.

## Stage 3 changes (owner 2026-11-16)
- **Evidence:** [`build/stage3h/`](build/stage3h/) ([`checks.json`](build/stage3h/checks.json), all passed); the
  rework-4 layout checks re-run: [`build/stage3f/`](build/stage3f/) all passed (phases measured equal, switch frames,
  zoom); `video/stage3f/` re-recorded.
1. **Equity from $0:** every curve starts at $0 / 0R (a start point the weekday before the first day, or at the first
   trade's entry in real-trades phases); the price scale always includes 0. Demo / prop / live: one point per trade at
   its exit time, each trading day labelled once along the bottom (`view.equity(per_trade=True)`; test
   `test_equity_starts_at_zero_and_real_trades_per_trade`).
2. **KPI tiles:** fewer than 2 points: the dashed line again; no note under profit factor ∞ (tooltip kept).
3. **KPI tiles:** 10 px between the number and its badge (tiles 200 px high in every phase).
4. **Scope chips:** sized to their text, 8 px padding each side; below 1,200 px their row has its own line.
5. **Stats grid:** each cell lights up on hover like the KPI tiles (Spotlight).
6. **Payout cycle:** the note under a funded account's summary ("assumed until the engine records payouts (step 22)");
   the optional **Last payout date** in the Accounts editor (funded), used by the "from today" Monte Carlo
   (`tests/test_perf_account_mc.py::test_the_cycle_starts_at_the_last_payout_date`). Not shot: the owner's panel has
   evaluation drafts only, and the scratch panel is gone (item 7).
7. **Scratch panel** on 8734 stopped and its scratch data removed.

## More stage 3 changes (owner 2026-11-16)
- **Evidence:** [`build/stage3i/`](build/stage3i/) ([`checks.json`](build/stage3i/checks.json), all passed); the earlier
  checks re-run on this build: [`build/stage3h/`](build/stage3h/), [`build/stage3f/`](build/stage3f/) (phases measured
  equal with the new summaries), all passed.
1. **Profit factor** with no losing trade: "—" as originally (no ∞, no note, no tooltip).
2. **Month picker** (`MonthPicker.tsx`): our own dropdown beside prev / next arrows, the arrow next to the text, themed
   hover / selected states, newest first, six months visible then it scrolls, ↑ ↓ Home End Enter Esc. The calendar now
   follows the page's phase (Development shows its months only; `test_calendar_follows_the_phase`).
3. **A real account's Drawdown:** one point per trade at its exit time, from the account's start balance, each trading
   day labelled once (`view.account_trade_run`); the floor in force during each session, the summary's floor the one
   set at the last close (equal to the Monte Carlo's "floor today").
4. **Monte Carlo summaries** (`mcpanels.tsx`), account and plan in the same cards, two variants:
   - **A (default):** the headline chance as an animated ring with its legend beside the account's position bar (floor
     → target or payout line, today's balance on it) or the plan's payouts / net cash; four stat cards with range bars.
   - **B (`?variant=b`):** one wide card: the headline number with an animated partition bar, beside the position bar /
     payouts; the same four stat cards.
   Every card lights up on hover like the KPI tiles; labels muted, key numbers large.

## Stage 3 polish (owner 2026-11-16; summary variant A kept)
- **Evidence:** [`build/stage3j/`](build/stage3j/) ([`checks.json`](build/stage3j/checks.json), all passed).
1. **Summary tightened:** cards px-3.5 py-3, gaps 10 px, the stat cards hug their content; legend values beside their
   labels ("pass first 77.6%", 8 px apart, measured); "3 days"; the header lines short ("Monte Carlo · 1,000 paths",
   "Monte Carlo · from today", "Drawdown · backtest replay", "Drawdown · real trades") with the details in the info
   tooltip; uppercase labels on the page at 0.05 em, 10.5 px, weight 400.
2. **Ring:** 124 px with a 7 px stroke, the number 19 px and its label 9 px: at least 14 px clear inside the ring
   (measured on both summaries at both widths); shots `ring_plan_*`, `ring_account_*`.
3. **Hover rule** for every list: the month picker and the preset picker now clear their highlight when the pointer
   leaves (a click-open highlights nothing; the keys do); the account switcher and the command palette already did.
   `tests/test_ui_hover_rule.py` fails any list that highlights on hover without clearing on leave; the browser check
   covers the month picker and the account switcher.
4. **Drawdown stats rows** (strategy and account) light up on hover like the main stats grid (every `StatGrid`).
5. **Settings section list** (`lib/scrollspy.ts`): "Danger zone" is active on click, while scrolling and after; at the
   bottom of the page the last section on screen is active. Settings is the only page with one today.
6. **Footer gap:** 80 px from the end of the content to "CREAMFLOW · NQ" on Performance (was 120), Accounts, the account
   editor and Settings (were 104): the footer's top margin 40 px on every page, no extra page padding.

## Monte Carlo summary refinements (owner 2026-11-16)
- **Evidence:** [`build/stage3k/`](build/stage3k/) ([`checks.json`](build/stage3k/checks.json), all passed: measured
  alignment, padding, the centred position block); the phase-switch and layout checks re-run on this build:
  [`build/stage3f/`](build/stage3f/), [`build/stage3j/`](build/stage3j/), all passed; `video/stage3f/` re-recorded.
1. **Pass card:** the label, legend and order luck on the left, the ring on the right (account and plan alike).
2. **Where it stands today:** the label at the top, the bar with its labels centred below it as one block (equal space
   above and below, measured within 4 px); the card as tall as the pass card beside it. The plan's payouts card the same.
3. **The four stat cards:** one structure (label · the value with its visual beside it · footnote): values, visuals and
   footnotes level across each row, each value centred on its visual (within 1.5 px). New visuals: "Days traded so far"
   as pips, one per day traded, the minimum days marked (Rapid EOD: 4; `min_trading_days` from the account's own
   stage); "Risk per trade" as a bar of the risk against the daily cap ($333 of $450; without a cap, of the drawdown).
4. **Plan view:** the same cards, so each number sits level with the middle of its range bar.
5. **Padding:** 16 px on all four sides of every summary card (measured).

## The real account's Drawdown summary row (owner 2026-11-16)
- **Evidence:** [`build/stage3l/`](build/stage3l/) ([`checks.json`](build/stage3l/checks.json), all passed): mffur1 (an
  evaluation, no lock) on the owner's panel, and three funded scratch accounts with a lock on a scratch panel
  (127.0.0.1:8734, its own data root in the scratchpad; removed after): Lucid Flex (not locked yet), MFFU Rapid
  (locked), MFFU Pro 50k (locks at the first payout); `row_*` close-ups at 1440 and 960 px. The layout checks re-run:
  [`build/stage3f/`](build/stage3f/), all passed.
1. Every cell has a value and a single-line sub-line (the full text in its tooltip); values and sub-lines level in
   each row (measured).
2. **Room to the floor:** "N% of the $X drawdown" and a thin bar of the room left, turning from green towards red as
   it shrinks.
3. **Floor locked** from the account's own rules (`lock_usd`, `drawdown_lock_trigger`, `payouts_taken`): no lock →
   "No lock" · "this plan's floor always trails"; a static floor → "Static"; a lock not reached → "$X to go" · "locks
   at $25,100" (or "… or 1st payout") with a progress bar; a first-payout lock before it → "At 1st payout" · "then
   locks at $50,100"; locked → "Locked" · "at $25,100 since <date>".
4. **Closest approach** now also counts the room left after each session's close (the floor that close set), so it is
   never above today's room (it was $1,576 for mffur1 against today's $1,000).
