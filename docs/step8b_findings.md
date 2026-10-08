# Step 8b findings (2026-10-07)

The gate's first run on the five step-7 sample days (2026-03-06, 03-11, 03-18, 03-24, 03-26; development month 6) failed every condition. These are the read-only checks the owner asked for afterwards (2026-10-07). **No gate limit or definition has changed.** The open decisions are SPEC §13 "Step 8b" Q8b-1 to Q8b-4. Reports from the runs are on the SSD in `<reports_dir>/step8b/`. The study scripts ran from a scratch folder and are not in the repo; this file records their method and numbers.

## 1. First run (as built)

| Chain | F1.6 p95 | `mapping_p95_max` | Spot-lag peak (03-06 / 03-11 / 03-18 / 03-24 / 03-26) |
|---|---:|---:|---|
| QQQ | 4.63 NQ | 1.0 | −1 / −1 / +1 / +1 / +1 s |
| NDXP | 5.73 NQ (max 230) | 0.5 | −1 / −1 / −46 / +1 / 0 s |
| SPY | 1.04 ES | 0.25 | −1 / −1 / +1 / +1 / +1 s |
| SPX | 0.68 ES | 0.25 | 0 / 0 / +1 / +1 / +1 s |

Atlas crossings within `atlas_match_tol`: 124/258, 71/153, 75/156, 107/266, 126/201. These counts come after the double-count fix in §6; before it, the five days had 1,311 crossings.

## 2. Timestamps

**Databento:** `ts_event` is the matching-engine time. `ts_recv − ts_event` is 0.3 ms median and under 10 ms at p99 on every sample day, so the futures side has no timing problem.

**Skylit `asOf` uses two conventions in history.** We found the switch date by scanning every backfill day, one mid-session window per day:

| Period | `asOf` | Where the board's `spot` sits in time (best match to the futures, 10 ms steps, from the whole second of `asOf`) |
|---|---|---|
| 2025-10-07 … 2026-03-16 | whole seconds | QQQ/SPY: **+0.63 to +0.75 s** (the board holds the state near the end of the second it is stamped with, a bucket-start label); SPX: −0.10 to −0.11 s |
| 2026-03-17 … 2026-09-30 | with milliseconds (published 0.5–0.95 s after the whole second) | every chain **−0.04 to −0.09 s** (a snapshot taken at the whole second, stamped when published) |
| Live stream (2026-10-07 recordings) | whole seconds; arrival − `asOf` p50 33–526 ms | not measurable yet (no futures for 2026-10-07 on disk). The content can't be later than the arrival, so the stream doesn't use the pre-03-17 history convention |

Per-day best offsets (ms from the whole second of `asOf`): QQQ 690, 630, −70, −70, −70; SPY 640, 750, −80, −50, −40; SPX −110, −100, −90, −80, −70. The correlation peak is broad for QQQ/SPY before 03-17 (r 0.41–0.51) and sharp elsewhere (r 0.57–0.82).

**Consequence for no-lookahead (§7 rule 1):** on QQQ/SPY boards before 2026-03-17, spot reflects the market up to about 1 s after `asOf`. The backtest applies a board at `asOf` + `map_latency_sec` (proposed 0.59 s), so in development months 1–6 those boards would carry about 0.1–0.4 s of the future. See Q8b-2.

## 3. NDXP spot

- In history, NDXP `spot` is **an NQ futures price, not the NDX index**:
  - every NDXP spot on every backfill day sits on the 0.25 grid;
  - on 03-06 it equals the NQH6 last trade at `asOf` + 1 s on 100% of boards (difference ≤ 2 pts; spot-change correlation 0.999);
  - on 2026-03-17 (the session before the NQ roll; NQH6 trades and NQM6 1-second bars are both on disk) each board equals exactly the NQH6 *or* the NQM6 last price at `asOf` + 1 s (median and p90 difference 0.00). It switches back and forth: 1,892 jumps, 52% of boards on M6.
- The jumps measure 216.75–218.75 pts (median per day). The NQH6→NQM6 spread was 219.75 at the 03-17 close (`processed/rolls/NQ_2026-03-18.json`). Jump counts: 03-06 0, 03-11 556, 03-17 1,892, 03-18 2,068, 03-24 124, 03-26 277. On 03-24/26 the jumps go *up* from M6 by +218.75, which NDX can't be (it trades below M6), so they point to NQU6. U6 isn't on disk, so this is not confirmed.
- `previousClose` is 0 for NDXP throughout March. On 2026-09-30 it is 30,339.33, which looks like the index. The live stream's NDXP spot moves in 0.01 steps (index-like).
- NDXP strikes are integer NDX strikes on a 10-point grid, not adjusted for futures.
- **Effect:** R = F ÷ spot ≈ 1.0000 in history, so NDX strikes land on NQ without the index-to-futures basis, up to about 220 pts off in March. The 230-pt worst case and the 8.6% F1.2 rejection rate come from the contract switching. NDXP can't be mapped from history as stored. See Q8b-1 and the Skylit question in §8.

## 4. Before/after p95 per chain and per fix

**Two measures.** "F1.6" is the gate metric: the step in a relevant node's level from one board to the next. A smoothed R passes it by construction. "Crossings" is the independent check the owner asked for: every time the board spot crosses a strike (QQQ/SPY every $1, SPX every 5), at the moment interpolated between the two boards, |futures then − strike × R in force at the previous board|. The futures price is read at the aligned time. QQQ is in NQ pts, SPY/SPX in ES pts. Limits: F1.6 1.0 / 0.25 / 0.25; crossings (`atlas_match_tol`) 1.0 NQ / 0.5 ES.

| Fix | QQQ F1.6 | QQQ crossings | SPY F1.6 | SPY crossings | SPX F1.6 | SPX crossings | NDXP |
|---|---:|---:|---:|---:|---:|---:|---|
| As built (F at `asOf`, per-board R, F1.2) | 4.63 | 5.66 | 1.04 | 1.25 | 0.68 | 0.75 | 5.73 F1.6; can't be fixed by these (§3) |
| 1. Aligned (F at the board's content time, per day/chain offset above) | 3.43 | 4.60 | 0.75 | 0.90 | 0.53 | **0.65** | n/a |
| 2. Aligned + trailing median 15 s | 0.50 | 3.80 | 0.11 | **0.81** | 0.10 | 0.84 | n/a |
| 2. Aligned + trailing median 30 s | 0.21 | **3.77** | 0.05 | **0.81** | 0.05 | 0.89 | n/a |
| 2. Aligned + trailing median 60 s | 0.10 | 3.79 | 0.03 | 0.83 | 0.03 | 0.93 | n/a |
| 2. Aligned + trailing median 120 s | 0.05 | 3.94 | 0.02 | 0.84 | 0.02 | 0.98 | n/a |
| 2. Aligned + trailing median 300 s | 0.02 | 3.94 | 0.01 | 0.83 | 0.01 | 1.00 | n/a |
| Floor: centered 120-s median (uses the future; evaluation only) | 0.06 | 3.88 | 0.02 | 0.84 | 0.02 | 0.89 | n/a |

Crossings counted: QQQ 2,317, SPY 2,770, SPX 3,972.

**Subsets of the crossing check (p95).** "Quiet" = the futures moved ≤ 2 NQ / 1 ES tick across the crossing's board interval. "Held" = spot stays past the strike for the next two boards. "Open" = the first 5 minutes of applied boards. "Roll day open" = the same for 2026-03-18, the first session on NQM6/ESM6. "Fast 1%" = the 1% of crossings with the largest 60-s futures move.

| Chain / fix | quiet | held | open | roll-day open | fast 1% |
|---|---:|---:|---:|---:|---:|
| QQQ aligned | 3.90 | 4.74 | 6.85 | 1.80 | 5.79 |
| QQQ 15 s | **3.57** | **4.43** | 5.42 | 2.66 | **4.47** |
| QQQ 30 s | 3.74 | 4.60 | 5.35 | 2.64 | 5.05 |
| QQQ 60 s | 3.94 | 4.52 | 5.22 | 2.67 | 5.14 |
| QQQ floor | 4.04 | 4.60 | 4.90 | 2.60 | 5.13 |
| SPY aligned | 0.80 | **0.86** | 1.51 | 0.65 | **0.91** |
| SPY 15 s | 0.75 | 0.95 | **0.88** | 0.92 | 1.33 |
| SPY 30 s | **0.68** | 0.99 | 1.01 | 0.86 | 1.38 |
| SPY floor | 0.70 | 1.00 | 0.89 | 0.87 | 1.39 |
| SPX aligned | **0.41** | **0.67** | **1.02** | 0.76 | 2.92 |
| SPX 15 s | 0.62 | 0.88 | 1.19 | 0.55 | **2.84** |
| SPX floor | 0.70 | 0.94 | 1.41 | 0.24 | 3.80 |

**How much the true R moves** (centered reference, futures points at spot), p50 / p95:

| | 5 min | 30 min |
|---|---|---|
| QQQ (NQ) | 0.34 / 1.06 | 0.59 / 1.89 |
| SPY (ES) | 0.07 / 0.22 | 0.13 / 0.42 |
| SPX (ES) | 0.19 / 0.56 | 0.24 / 0.72 |

The lag a 15–30 s trailing window adds is small: R moves about a twentieth of a point in 30 s. Against a centered reference, the lag error of a 30-s window is 0.97 NQ (QQQ) and 0.19 ES (SPY) at p95, versus 3.75 and 0.79 for the aligned per-board R. That reference is itself a median, so it favours smoothing; the crossing check above is the fair one.

**Method:** trailing only (never centered), over applied boards by their aligned time. It resets at the session's first applied board (09:30, or 09:32 for SPX/NDXP after `ratio_open_skip_sec`) and after any board gap > 10 s (an analysis choice; QQQ/SPY/SPX gaps were ≤ 2 s). It never spans two sessions or two contracts (the contract is fixed per session, §5.1). At the open it is the median of the boards so far. The crossing check uses the R in force at the board before the crossing.

## 5. What remains, and the recommendation

1. **Alignment is the biggest fix** on every chain: QQQ crossings 5.66 → 4.60, SPY 1.25 → 0.90, SPX 0.75 → 0.65; F1.6 down 22–28%.
2. **ETF tick noise and spot type.** QQQ/SPY `spot` is a last-trade price (whole cents, plus 0.5–11% half-cent midpoint prints, more before 03-17). One cent is about 0.4 NQ pt on QQQ and about 0.1 ES pt on SPY.
3. **The remaining QQQ/SPY error is mostly in the truth, not in R.** Even the floor scores QQQ crossings 3.9 NQ (quiet 4.0, held 4.6) and SPY about 0.84 ES. The tail isn't one-board stray prints (held crossings don't shrink it) or crossing-timing error (quiet crossings don't either). It is the moment-to-moment gap between QQQ's last trade and NQ, about 1.5–2 bp at p95. No R estimate gets the crossing check under 1.0 NQ / 0.5 ES at p95.
4. **Recommended windows (lowest p95 across all crossings, on these development days):**
   - **QQQ 30 s** (3.77). 15 s (3.80) and 60 s (3.79) are within noise; 15 s is better on quiet, held and fast-move crossings.
   - **SPY 15 s.** It ties 30 s at 0.81, has the shorter lag and a better open (0.88 vs 1.01). But the per-board aligned R is better in the fastest 1% (0.91 vs 1.33) and on held crossings.
   - **SPX: no window.** The aligned per-board R wins every crossing measure except the roll-day open and fast moves (about even).
   - **NDXP:** can't be evaluated until its spot is resolved.
   - A window cuts F1.6 well under `mapping_p95_max` for QQQ/SPY by construction, so the gate's metric no longer measures accuracy. See Q8b-4.
5. **SPX on its own (no ETF tick):** alignment takes F1.6 0.68 → 0.53, crossings 0.75 → 0.65 and fast moves 4.25 → 2.92. The remaining 0.53 ES pt between consecutive 1-s boards is the SPX index's own jitter against ES (the index is computed from component last trades and lags ES in fast moves). It is above the 0.25 limit with or without alignment, and smoothing makes the crossing check worse.

## 6. Atlas table: the same strike crossed twice

When spot landed **exactly** on a strike (common on QQQ/SPY, whose spot is on the cent grid while the strikes are whole dollars), the old code counted the touch twice. It counted the arrival (board j, spot = k) and the departure (board j+1, previous spot = k). Both were interpolated to board j's time, so they showed the same second. The departure row used the R of board j, whose own spot *is* the strike: k × F ÷ k = F, so it always showed 0.00 / "yes" and proved nothing. Fixed (commit of 2026-10-07): a touch is counted once, on its arrival, with the R in force before it. The tables were regenerated and the gate report now lists every sample day's table, with any missing day marked pending. `within_tol` is now stored as a JSON boolean (it was written as the string "True"/"False").

## 7. Not done (needs the owner's OK)

- The live stream's convention (§2) can be measured against Databento trades for 2026-10-07 (NQ/ES `trades`, included in Standard, $0 by `get_cost`; outside the step-8 plan). Tomorrow's `parity --session 2026-10-07` also compares the stream with history board by board.
- NQU6 1-second bars for 2026-03-24/26 ($0 included) would confirm the up-jumps are U6.

## 8. Draft question to Skylit (for the owner to send)

> Subject: NDXP `spot` and the `asOf` convention in `/v1/historical/range`
>
> 1. **NDXP spot in history.** In `/v1/historical/range`, NDXP `spot` always sits on a 0.25 grid and matches CME NQ futures last trades to the tick, not the NDX index. On 2026-03-17 consecutive boards switch between the March (NQH6) and June (NQM6) contracts, about 218 points apart. On 2026-03-24 and 03-26 boards jump about +218.75 above the June contract, which looks like September (NQU6). `previousClose` for NDXP is 0 in March 2026 and 30,339.33 on 2026-09-30. In the live stream, NDXP spot moves in 0.01 steps, like the index. Questions: (a) What is NDXP `spot` in history, and which contract is it on each board? (b) Were the NDXP per-strike gamma values computed against that futures price or against NDX? (c) Can history give the NDX index spot for each board (or at least each minute) over the last 365 days? (d) From which date does NDXP history carry the index spot, if it does now?
> 2. **What `asOf` means.** Up to 2026-03-16, `asOf` is a whole second, and QQQ/SPY `spot` matches the market about 0.7 s *after* `asOf` (SPX about 0.1 s before). From 2026-03-17, `asOf` has milliseconds (0.5–0.95 s past the second), and `spot` matches the market at the whole second just before `asOf` for all symbols. The live stream stamps whole seconds. Questions: (a) In history before and after 2026-03-17, and in the stream, is `asOf` the snapshot time, the publish time or the start of a 1-second bucket? (b) Which instant does `spot` describe? (c) What is the QQQ/SPY spot source (consolidated last trade, NBBO mid)?
> 3. **SPX `previousClose`** is 0 on 2026-03-06. Is that expected?
