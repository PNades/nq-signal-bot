# Performance (SPEC §9b item 6)

> Overrides MASTER.md for this page. The page mocked in step 14a part 0.

## Layout override
- Full width (no 1200-px cap: a dashboard). Header with selectors: account (All accounts or one account, each with its
  preset and phase as a sub-label; every view follows it), phase, date range (date-range picker), $ | R segmented
  control, Export CSV button.
- **KPI row** (6 cards, in this order; owner 2026-11-15): Net P&L, Trades, **Net R per trade** (the average net R per
  trade; replaces Net R), Win rate, Profit factor, Max drawdown; each with a sparkline that draws in and a number
  ticker; deltas with sign + arrow.
- **Tabs** (animated underline / pill): Equity · Drawdown · Monte Carlo · Calendar · Trades.
  - Equity: area chart with crosshair tooltip and phase shading (MASTER §11).
  - Drawdown (owner 2026-11-15): **each account under its own drawdown rule** (SPEC §6.7), never a fixed amount: the
    drawdown from its end-of-day high, and its floor drawn the same way (floor − high) as a stepped dashed line, so a
    breach is the drawdown reaching it. Static: start − drawdown. EOD-trailing: the end-of-day high − drawdown, with its
    lock: `floor` (stops rising at the lock), `first_payout` (Pro: trailing until the first payout, then the lock),
    `floor_or_first_payout` (Lucid Flex funded: locked at $25,100 when the floor gets there or at the first payout,
    whichever comes first). Once a floor locks or is static, the line falls away as the high rises (the room grows);
    a "floor locked <date> at <value>" badge says so. The header names the account, preset, phase and the rule in
    words; the tooltip shows drawdown, balance, floor and the room to it; the deepest point is marked. With All
    accounts, one panel per account (two columns from 1280 px).
  - Monte Carlo (owner 2026-11-15): per account, 1,000 reshuffles of its whole days, **each path run through the
    account's own rule** (its floor moves with that path's high and lock); the chance of reaching the floor and the
    median trading day it happens, in text; bands 5–95% and 25–75%, median, the smallest and largest drawdown paths,
    the backtest order and its floor. With All accounts, one panel per account.
  - Calendar: the P&L calendar (layout of `docs/design/pnl_calendar_reference.png`): month header stats, Mon–Fri +
    Week, day cells with hover cards, a month-change transition (slide 12 px + fade, instant in Calm), click to expand
    the day's trades in a side panel.
  - Trades: sortable, filterable table with sticky header, mono numbers right-aligned, column chooser.
- **Ledger guard:** walk-forward months never at trade or day level, sealed months not before step 20 (SPEC §9b): a
  refused range shows an inline notice naming the missing gate; backtest data labelled "backtest".
- **Dot-matrix footer** ends the page (owner 2026-11-15; 21st.dev Halftone Wordmark Footer 37628):
  - the word **"cream"**, lowercase, at every width, in heavy **Geist** (variable): Black (900) at full screen,
    getting lighter as the window narrows, SemiBold (600) at a 960-px window (straight in between by the footer's
    width, 945 → 1905 px);
  - sized from the footer's own box and again on every resize: the letters' ink fits the width minus the details'
    side padding on each side, so they never run off;
  - the details ("CREAMFLOW · NQ / LOCAL CONTROL PANEL · 127.0.0.1"), a hairline, a clear gap of
    max(32 px, 18% of the letter height), then the letters; the footer's height follows from that;
  - scrolled all the way down, the page ends inside the letters: their lower quarter is cut off by the bottom
    edge, with nothing below (the page cancels the dock's bottom padding);
  - dots part around the pointer and warm to ember; a still dot render in Calm.

## Performance budget
Charts render only the active tab; the trades table virtualises rows over 200; the Monte Carlo fan draws once per input
change (not per frame).
