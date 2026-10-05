# Owner notes

How I trade. Claude Code reads this to write `docs/feature_spec.md`. Answers written 2026-10.
- All times are ET.
- When these notes disagree with the Skylit docs, these notes win.
- Anything left out here: Claude Code proposes a starting value, the backtest tunes it, and the step-12 visual check and step-14 review cards catch definitions that don't match what I see. There are no dated trade examples.

---

## Skylit nodes
- A node is decently sized if it is at least 25% of the King node on its map.
- Node types, positive vs negative nodes, rugs, pika clouds, rolling ceilings/floors, long vs short gamma: as in the Skylit docs (`docs/skylit/`).
- QQQ and NDXP matter most for NQ (NDXP has the real edge; the NDX root isn't used). A QQQ floor or ceiling that SPY and/or SPX are also approaching at the same moment (on ES) makes the level a lot stronger.

## Opening-range fib golden zone
- Orientation: drawn on the most recent swing. If price went high then low, the fib is drawn from the top to the bottom (and the reverse).
- Any of the fib levels (0.5 / 0.618 / 0.705 / 0.786) can play out.
- If price settles past 0.786, the setup is invalidated.

## Chart levels
- I don't know the ideal zone widths for POC, LVNs, single prints, VWAP and bands, highs/lows or IB high/low: the model should learn them.
- Breaks and retests depend on the level. Use the level flip rule and learn the buffer around each type of level.

## Entries
- Absorption: lots of buyers at the top and price starts pushing down off them = the buyers were absorbed, and you can see the push down. Same at the bottom with sellers.
- I spot absorption on the footprint or as CVD divergence; there are several tools that show it.
- Buyers (or sellers) that get absorbed **and** exhausted is a great signal.
- Big trades don't really matter: CVD and the footprint show the same thing.
- Failed audit / audited hold: price can go as deep into the zone as it wants, for as long as it wants; it just can't settle on the other side of it.
- Breaking a level: a 1-minute candle settling past it breaks a chart level; trading slightly past without settling doesn't. Skylit nodes are different (price trading past the band breaks them), and sweeps at highs/lows are their own case (price goes past and comes back to the level).
- Absorption stop: about 5 points past the absorbed area. I like to see price start to push away from the absorbed area before entering.

## Trade management
- Stop buffer and stop type: not sure (let the backtest decide).
- One target per trade is fine.
- Breakeven: if the target is more than one structure away and a 1-minute bar settles past the first structure, move the stop to breakeven. This is how I view it (a starting point), not a must-use: the model should learn what works best.
- I'm fine trading any time in the session (09:30–16:00, last entry 15:45), within my setup windows: nothing before 09:50, only the golden zone 09:50–10:00, everything from 10:00.
- News: nothing open and no orders resting from 30 minutes before every high-impact release until 60 minutes after it. High-impact = my ForexFactory list: NFP (with unemployment and hourly earnings), FOMC decision and statement, FOMC press conference, FOMC minutes, Fed Chair testimony and speeches, CPI, Core PCE, PPI, Advance GDP, ISM Manufacturing, ISM Services, Retail Sales. These always count, whatever ForexFactory rates them that week, and every scheduled Fed Chair testimony and speech counts. On top of that, live only: any other US event ForexFactory marks red (High) at the 08:30 pre-open also gets the blackout (past red ratings aren't available, so the backtest uses my list only; live is a little stricter on purpose, and each extra one is logged). Other releases don't block trading (2026-10-04).
- I watch the news myself: for testimony that runs long or anything unscheduled, I pause or flatten the bot by hand. Gameplan blackouts aren't used; this rule replaces them.

## Accounts (2026-10)
- MyFundedFutures only: **Rapid EOD 25k** and **Builder 25k**. Max 2 minis / 20 micros on both. Both max losses are EOD trailing ($1,000) and lock at start + $100.
- MFFU on every account: 7-day inactivity rule, no maximum evaluation length, consistency measured on net daily P&L (after commissions and fees). Commissions round trip with all fees: $4.68 per NQ, $1.90 per MNQ. I handle account fees myself.
- Later I may scale up to MFFU 50k or 100k accounts of the same types: same strategy, bigger size. No retraining; I just enter the new rules on the Accounts page.
- Rapid EOD eval: $1,500 target, no DLL, 30% consistency, at least 4 trading days, news allowed. Funded: no DLL, no consistency, $1,100 buffer, then payouts of $500 minimum with no maximum (each later payout needs $250 net profit since the last), daily payouts, 7-day inactivity rule, up to 3 funded accounts at once.
- Builder eval: $1,500 target, $600 soft DLL, no consistency (can pass in one day). Funded: $600 soft DLL, $1,100 buffer plus up to $1,000 payout ($2,100 total), 50% consistency rule → $1,050/day for the first payout, so each payout takes at least 2 trading days. After it the buffer is built, so each payout needs another $1,000 and the 50% rule makes it $500/day.
- On funded accounts MFFU allows no positions or orders from 2 minutes before to 2 minutes after Tier 1 news (FOMC, FOMC minutes, jobs report, CPI). My own news rule (above) covers it on every account.
- No hedging: never long on one account and short on another at the same time.
- I activate funded accounts and request payouts myself on MFFU's site; clearing the buffer is not something I confirm.
- Each account applies its own thresholds: one setup can trade on one account and be skipped on another (e.g. an account one loss from failing is stricter).
- Accounts change over time: when one passes I point its TradersPost connection at the next eval. Funded and eval accounts have different rules, so they don't copy each other.

## Settings I use
- Deepcharts volume profile and VWAP: value area 70%, profile from 18:00 ET, VWAP bands 1σ/2σ, 30-minute TPO (as the SPEC assumes). On contract rolls Deepcharts back-adjusts history: older data is shifted by the price gap between the expiring and the new contract, so levels carry over without a gap.
- Atlas: I use the QQQ and SPY charts, always the front (0DTE) expiration (it matters most for scalping); when QQQ/SPY reach a level, NQ/ES reach it at the same time.

## Gameplans
- 28 archived gameplans, 2026-08-12 to 2026-10-02 (Google Drive folder "Jeron gameplans"; two are named by event: NFP = 2026-09-04, FOMC = 2026-09-16).
- All were finished before 09:30 (no after-the-fact notes). I saved them to Drive later in bulk, so their Drive times don't show when they were written. A new one is made almost every morning.
