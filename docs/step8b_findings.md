# Step 8b findings (2026-10-07; rounds 2 and 3 2026-10-08)

The gate's first run on the five step-7 sample days (2026-03-06, 03-11, 03-18, 03-24, 03-26; development month 6) failed every condition. These are the read-only checks the owner asked for afterwards (round 1 2026-10-07, round 2 2026-10-08). **No gate limit or definition has changed.** The open decisions are SPEC §13 "Step 8b" Q8b-1 to Q8b-4. Reports from the runs are on the SSD in `<reports_dir>/step8b/`. The study scripts ran from a scratch folder and are not in the repo; this file records their method and numbers.

**Round 3 (the table and R7–R10, first below) is the current proposal; round 2 (R1–R6) supersedes the round-1 recommendations in §4–§5.** Round 1 mapped each crossing with an R that included the crossing pair's first board, so it shared that board's spot. It also used only the five sample days, with offsets fitted per day.

## Round 3 (owner's answers of 2026-10-08, items A–F)

**The table** (22 development days, event moments = crossings of relevant nodes, R known before the crossing, each chain with its approved/chosen estimator; QQQ/NDXP in NQ pts, SPY/SPX in ES pts):

| Chain (estimator) | Crossing p95 | 1-s futures move p95 | Level error p95 | Bias (crossing / level) | Proposed stop floor |
|---|---:|---:|---:|---|---|
| QQQ (trailing 60 s) | **4.00** (still futures 3.36) | 5.75 | 0.55 | +0.06 before 03-17, +0.01 after / −0.003 [−0.009, 0.000] | **4.0 NQ** (crossing p95; A did not confirm timing) |
| SPY (trailing 60 s) | **0.78** (still 0.64) | 1.25 | 0.11 | ≈ 0.00 / 0.000 [0.000, 0.000] | none for NQ stops (ES reads only); band check 0.78 ES |
| SPX (per board, aligned) | **0.74** (still 0.51) | 1.25 | 0.64* | +0.01 / −0.009 [−0.020, 0.000] | none for NQ stops (ES reads only); band check 0.74 ES |
| NDXP (method choice pending) | n/a (history spot is NQ) | 5.75 (NQ) | day-ahead p95: basis + carry **15.1**, QQQ × k (N = 5) **13.8**; live 10-07: basis 14.4 / 10.0, QQQ × k 4.6–7.9 / 2.9–6.3 | +0.22 (basis) / +2.2 (QQQ × k) | **≈ 14–15 NQ** pending the method choice and the recordings (owner decides whether NDXP sets the floor) |
| Chart levels (VWAP, POC, LVN, IB, …) | — | — | — | — | **0** (no mapping) |

\* SPX: the level error is measured against a 2-minute centered median, which favours smoothers by construction. On the crossing test, SPX's per-board R beats that median (0.74 vs 0.78), so 0.64 overstates SPX's real level error.

## Owner's decisions (2026-10-08, after round 3)
- **Approved and written into SPEC §3.1/§5.1/§6.2, feature_spec F0.1/F1.1/F1.2/F1.7/F7 and config:** the ingest rule (τ = floor(`asOf`) + 1 s, F at the content time `board_content_offset_ms`); R = QQQ 60 s, SPY 60 s, SPX per board; `stop_buffer_floor` QQQ 4.0 NQ (the crossing p95), chart levels 0, SPY/SPX none.
- **Pending:** NDXP's method and stop floor (lower intraday p95 against the real NDX on the recordings; ±20 band kept until the step-13 reaction-distance report); SPY's post-03-17 content offset (re-measured on the recordings).
- **Q8b-4 answered (owner 2026-10-08):** gate = (a) bias interval contains 0 or within one futures tick, (b) chosen estimator p95 within 10% of the best possible R, the alignment check in place of spot lag, the Atlas check; F1.6 a diagnostic. Commands for (a)/(b) and `step8b alignment` are still to build.
- **Recordings:** `--maps NDXP,QQQ,SPY` on 10-09, 10-12, 10-13 (≈ 1,185 credits each, ≤ 1,254 with 12 reconnects); $0 Databento NQ and ES trades for each recorded day, priced first.

## R14. Recording 2026-10-09 (NDXP, QQQ, SPY; owner 2026-10-10)
`nqbot step8b recorded-day 2026-10-09 --with-prev`: $0 `get_cost` for NQZ6 and ESZ6 trades 10-09 and NQZ6 10-08 (pulled). 23,265 NDXP / 23,375 QQQ / 23,120 SPY boards (09:27–16:00; QQQ/SPY's first board is 10-08 16:14:59, the previous evening's, sent on connect and never applied before 09:30).

- **NDXP candidates against the real NDX** (22,784 boards scored, after the gap window below): basis + carry p95 16.49 NQ (median +13.09); QQQ × k N = 5 p95 6.79 (+3.79), N = 10 5.13 (+2.12), N = 20 5.08 (−2.37). QQQ × k uses the QQQ board in force by its apply time. The method is chosen by `step8b ndxp-select` after the third recording.
- **Live content offsets (fitted):** SPY **+560 ms** (10-07: +300/+420; March history −60), QQQ −320 ms (configured live −60; 10-07 −80/−140), NDXP +40 ms. None after floor(`asOf`) + 1 s: no alert. SPY's live offset is set from the three recordings.
- **Reconnects:** 7 (10:27, 11:27, 12:27, 13:27, 15:09 `max_duration`; 14:09, 15:26 `server_shutdown`), each 0.2–0.4 s. `gapCovered=False` at 10:27 (NDXP advanced 14:27:03 → 14:27:04 UTC), 12:27 (QQQ 16:27:04 → 16:27:05) and 15:09 (NDXP 19:09:46 → 19:09:47): a one-second advance each, and the recording has every second's board around all three (±10 s), so **no board was missed**. The other missing seconds near reconnects (SPY 11:27:04, NDXP/SPY 14:09:50) are ordinary single missing boards, which occur all day (NDXP 186, SPY 282, QQQ 26 single-second gaps; NDXP also has longer publishing gaps unrelated to reconnects, e.g. 26 s at 10:15:03 ET, which the 10-s `ratio_gap_reset_sec` resets).
- **Window left out of the 8b numbers:** boards whose content time falls from 2 s before to 60 s after each `gapCovered=False` resume. 2 s before covers the `lastAsOf` → `currentAsOf` step; 60 s after is the longest trailing R window (QQQ/SPY 60-s median), during which a missed board would still sit in the R in force. 185 NDXP boards left out on 10-09; every p95 moves by < 0.01 NQ (as expected: nothing was missed). `recorded-day` applies it to every recording (`step8b_recorded.uncovered_gaps`, a test).
- **10-07:** the two 15-minute recordings had no `gapCovered=False` (the 10:06 `server_shutdown` ×4 all resumed covered; the 15:06 one too). **10-08:** not recorded.
- **Live content offsets on every recording (owner 2026-10-10; `asOf` vs Databento `ts_event`, so the laptop clock does not enter):** QQQ −80 ms (10-07 10:01), −140 ms (10-07 14:59; peak −260…−120), −320 ms (10-09; peak −360…−260; per hour −260, −300, −220, −280, −240, −320, −300); SPY +520 (10-07 10:01), +320 (10-07 14:59; peak +260…+320), +560 ms (10-09; peak +440…+620; per hour +580, +540, +620, +540, +440, +580, +500). The upper edge of every correlation peak (offsets within 0.01 of the best) stays below +1000 ms, so **no live board's content falls after floor(`asOf`) + 1 s and the alignment check would not alert**. QQQ's −320 ms on 10-09 against the configured −60 means F is read ≈ 0.26 s late there (an R error, not lookahead). QQQ joins SPY in Wednesday's live-offset decision (§13 Q8b-2); no setting changed. 10-12 and 10-13 are measured the same way.
- **Clock bound:** a board cannot arrive before its content, so min(arrival − content time) < 0 bounds how far the laptop clock was behind. 10-09: QQQ median −0.52 s, SPY −0.58 s (0.1% tail −1.31 s): the clock was behind by **at least ≈ 0.6 s**. 10-07: SPY's 0.1% tail −0.07 s, QQQ ≥ +0.14 s: the clock was close, possibly ≈ 0.1 s behind. A clock behind makes arrival − `asOf` smaller, so `map_latency_sec` 0.59 (10-07 14:59, p99.9 0.585 s) may be understated by about that much; content offsets and the gate are unaffected. The owner started the Windows Time service and resynced on 2026-10-10; `map_latency_sec` is re-checked on the 10-12 and 10-13 recordings (same bound, and `step7`'s delay measure) before anything changes.
- **Which way the clock biases `map_latency_sec` (owner 2026-10-10):** the delay is arrival (laptop clock) − `asOf` (Skylit's clock). A laptop clock running δ behind stamps every arrival δ too early, so the measured delay is the true delay − δ: a clock behind makes `map_latency_sec` **too small**, never too large. 0.59 was measured on 10-07 (14:59 recording, p99.9 0.585 s), not 10-09; that day's bound says the clock was at least ≈ 0.07 s behind, with no upper limit, so the true 10-07 delay was at least ≈ 0.66 s and possibly more. (10-09's clock, ≥ 0.6 s behind, would have understated it by ≥ 0.6 s.) Too small is the risky direction: the backtest would apply a board up to δ before live could have it (a small lookahead). **Decision (owner 2026-10-10):** the 10-12 and 10-13 recordings, with the clock synced, re-measure the delay (p99.9 over both, and the arrival − content bound to confirm the clock); Wednesday sets `map_latency_sec` from them before the gate re-run, and if the value changes, the gate (bias, estimator), the Atlas tables and the volatility numbers are re-run with it. Content offsets, alignment and the gate's `asOf`-based timing don't use the laptop clock and are unaffected.
- **Clock:** arrival − `asOf` was negative on 10-09 (QQQ p50 −0.84 s, SPY −0.02, NDXP −0.20; on 10-07 +0.04 / +0.42 / +0.12) and drifted ≈ −0.1 s over the session for all three; the Windows Time service was not running (`w32tm`: "The service has not been started"). The per-symbol shifts differ, so part is Skylit's stamping per symbol and part is the laptop clock. §13 Q21-resume notes it for §9 Clock.

## R13. Mapping dislocations, months 1–6 (owner 2026-10-09; run by the owner, finished 2026-10-12)
`nqbot step8b dislocations` (read-only): R moving more than 0.05% / 0.1% / 0.2% within 2 minutes (trailing range), events merging flagged boards less than 120 s apart, on two series: the **per-board R** (each board's own F ÷ spot at its content time) and **the bot's R** (F1.7: QQQ/SPY trailing 60-s median, SPX per board with F1.2's outlier rule, at each board's apply time). 121 trading days counted (2025-10-07 … 2026-03-31: 110 before the 2026-03-17 stamping change, 11 from it), 4 skipped as expected (2025-10-01/02/03/06, no Skylit boards), 0 left uncounted; the 44 older day files without the bot's-R series were recounted first. Full per-day table: `<reports_dir>/step8b/dislocations.md`.

**Side by side, events per-board → bot's R (days with at least one; events per day in brackets):**

| Chain | Threshold | Before 03-17 (110 days) | From 03-17 (11 days) | Total (121 days) |
|---|---|---|---|---|
| QQQ | 0.05% | 1,515 → 2 (109 → 2 d; 13.8 → 0.02/d) | 102 → 3 (11 → 2 d; 9.3 → 0.27/d) | **1,617 → 5** (120 → 4 d) |
| QQQ | 0.1% | 264 → 0 (63 → 0 d) | 11 → 2 (2 → 1 d) | **275 → 2** (65 → 1 d) |
| QQQ | 0.2% | 2 → 0 | 4 → 1 (2 → 1 d) | **6 → 1** (4 → 1 d) |
| SPY | 0.05% | 768 → 0 (90 → 0 d; 7.0 → 0/d) | 58 → 4 (11 → 3 d; 5.3 → 0.36/d) | **826 → 4** (101 → 3 d) |
| SPY | 0.1% | 23 → 0 (15 → 0 d) | 8 → 1 (2 → 1 d) | **31 → 1** (17 → 1 d) |
| SPY | 0.2% | 0 → 0 | 3 → 1 (2 → 1 d) | **3 → 1** (2 → 1 d) |
| SPX | 0.05% | 46 → 37 (35 → 30 d; 0.42 → 0.34/d) | 86 → 57 (11 → 10 d; 7.8 → 5.2/d) | **132 → 94** (46 → 40 d) |
| SPX | 0.1% | 1 → 0 | 21 → 11 (8 → 4 d) | **22 → 11** (9 → 4 d) |
| SPX | 0.2% | 0 → 0 | 3 → 2 (1 → 1 d) | **3 → 2** (1 → 1 d) |

- **QQQ/SPY: on the bot's R, dislocations nearly vanish.** The per-board R's 1,617 / 826 events at 0.05% are spot noise (the ETF last trade, R4/R7), which the 60-s median removes. What is left is 2026-03-30/31: at 0.1%, QQQ 2 events and SPY 1, all on 03-31; at 0.05%, outside 03-30/31 only QQQ 2 (2026-01-23, 2026-02-25) and SPY 1 (2026-03-23). In 121 sessions the bot's QQQ/SPY R had one real ETF-vs-futures dislocation day (03-31 12:40–12:47, the R12 tail, R ≈ 0.33% in two minutes) and one smaller one (03-30).
- **SPX barely changes and rises after 03-17.** SPX's R is per board, so the bot's R stays close to the per-board R (132 → 94 at 0.05%; F1.2's outlier rule removes about a third). Before 03-17 it is quiet (0.34 events per day at 0.05%, none at 0.1%); from 03-17 it is 5.2 per day at 0.05% (2.9 per day without 03-30/31) and 11 events at 0.1% (3 without 03-30/31). It is not just busier markets: the busiest days before 03-17 (ES mean 1-minute range above 5) had 0–2 events at 0.05%, the 11 days from 03-17 (ES 2.9–5.2) a median of 4 (0–20); at a similar ES range SPX's median 2-minute range of R is also higher (e.g. 03-17 1.96 bps at ES 2.89 vs 12-02 1.68 at 3.09; 03-31 3.40 at 5.15 vs 03-06 2.32 at 5.45). The rise starts with the 2026-03-17 stamping change; SPX's median content offset is −80 ms in both eras (R4), so the median did not move. Whether these moves are noise or real was tested read-only (§13 Q8b-6): a trailing median makes SPX's crossing error **worse** in both eras (p95 from 03-17: per board 0.809 ES, 30 s 0.842, 60 s 0.878), so at least part of the movement is real and the per-board R follows it; smoothing lags it. Only 11 days lie after the change, so the size of the rise is uncertain.
- **The 0.1% events on the bot's R, by time of day** (ET, minutes after the open; none in a news blackout):

| Chain | Date | Start | Min after open | Flagged (s) | Peak range | First 15 min |
|---|---|---|---:|---:|---:|---|
| QQQ | 2026-03-31 | 09:30:03 | 0.1 | 124 | 0.159% | yes |
| QQQ | 2026-03-31 | 12:41:39 | 191.7 | 411 | 0.334% | |
| SPY | 2026-03-31 | 12:41:39 | 191.7 | 365 | 0.323% | |
| SPX | 2026-03-24 | 10:52:00 | 82.0 | 141 | 0.146% | |
| SPX | 2026-03-27 | 11:23:57 | 114.0 | 118 | 0.117% | |
| SPX | 2026-03-27 | 13:07:17 | 217.3 | 109 | 0.100% | |
| SPX | 2026-03-30 | 09:32:15 | 2.3 | 118 | 0.179% | yes |
| SPX | 2026-03-30 | 13:47:19 | 257.3 | 125 | 0.126% | |
| SPX | 2026-03-30 | 15:50:09 | 380.2 | 114 | 0.102% | |
| SPX | 2026-03-31 | 09:32:22 | 2.4 | 156 | 0.503% | yes |
| SPX | 2026-03-31 | 09:49:28 | 19.5 | 153 | 0.175% | |
| SPX | 2026-03-31 | 12:40:00 | 190.0 | 775 | 0.879% | |
| SPX | 2026-03-31 | 13:36:43 | 246.7 | 124 | 0.158% | |
| SPX | 2026-03-31 | 15:51:02 | 381.0 | 68 | 0.106% | |

  Per-board R for comparison (0.1%, time of day 09:30–10:00 / 10:00–11:00 / 11:00–14:00 / 14:00–15:00 / 15:00–16:00): QQQ 82 / 76 / 99 / 11 / 7 (24 in a news blackout, 51 in the first 15 minutes); SPY 8 / 8 / 13 / 2 / 0 (3, 4); SPX 3 / 6 / 11 / 0 / 2 (1, 2). The per-board noise sits in the morning and around news; the bot's R has none of the news cases.
- **SPX trailing-median what-if (read-only, 2026-10-13; 22 gate days, the same 6,951 crossings, §5.2 relevance, `map_latency_sec` 0.59; config unchanged; `<reports_dir>/step8b/spx_window_whatif.json`):**

| SPX R | Gate p95 (ES) | vs best 0.785 (limit 0.863) | p95 before / from 03-17 | p99 | Bias, median [95% interval] |
|---|---|---|---|---|---|
| **per board (current)** | **0.779** | 0.993 pass | 0.759 / 0.809 | 1.274 | +0.008 [−0.002, +0.014] |
| trailing 30 s median | 0.808 | 1.030 pass | 0.780 / 0.842 | 1.292 | −0.009 [−0.024, +0.003] |
| trailing 60 s median | 0.849 | 1.082 pass | 0.825 / 0.878 | 1.357 | −0.016 [−0.030, +0.002] |
| trailing 120 s median | 0.873 | 1.112 **fail** | 0.859 / 0.889 | 1.328 | −0.010 [−0.028, +0.004] |

  Per board is best at every window, in both eras and in the tail. SPX's spot is the index (computed from its components), not an ETF last trade, so its board-to-board R carries little spot noise and a median only adds lag (QQQ/SPY are the opposite, R4/R7).
- **Consequence:** §13 Q11-instab (recommendation there) and §13 Q8b-6 (SPX estimator: keep per board). Nothing changed.

## R12. Does the crossing error scale with market speed? (owner 2026-10-08; re-run 2026-10-09 with `map_latency_sec` 0.59)
The 22 development days of R1, event-moment crossings with the approved estimators (QQQ 60 s, SPY 60 s, SPX per board), each crossing using the board in force by its apply time (floor(`asOf`) + 1 s + 0.59 s). **Trailing volatility** = the mean high-low range of the completed 1-minute futures bars in the 15 minutes before each crossing (only bars that ended at or before it: no lookahead). The day's volatility in the per-day tables is the session's mean 1-minute range (descriptive only). Errors are |futures − strike × R|. SPX has no NQ stop floor; its fixed column is its crossing p95 (0.78 ES), for comparison. Result files: `<reports_dir>/step8b/volstudy/`.

### Before → after `map_latency_sec` 0.59 (the same code at 0 s and 0.59 s)

| Chain | Spearman vs vol15 | Q1 → Q4 p95 | Scaled floor min | Slope k | Months 5–6 coverage, scaled | Same, fixed | Mean floor, Q1 / Q4 |
|---|---|---|---|---|---|---|---|
| QQQ | 0.293 → 0.293 | 2.53–5.92 → 2.53–5.98 | 2.77 → 2.79 | 0.195 → 0.195 | 96.5% → 96.5% | 95.3% → 95.3% | 2.77 / 6.72 → 2.79 / 6.73 |
| SPY | 0.2 → 0.198 | 0.52–1.0 → 0.52–1.0 | 0.52 → 0.53 | 0.2 → 0.2 | 96.6% → 96.6% | 95.3% → 95.2% | 0.56 / 1.34 → 0.56 / 1.34 |
| SPX | 0.248 → 0.236 | 0.47–0.93 → 0.52–1.01 | 0.45 → 0.52 | 0.165 → 0.165 | 93.7% → 93.7% | 94.9% → 94.2% | 0.45 / 1.06 → 0.52 / 1.06 |

Months 5–6 coverage per quartile, before → after (scaled / fixed):

| Chain | Q1 | Q2 | Q3 | Q4 |
|---|---|---|---|---|
| QQQ | 97.2% / 99.4% → 97.3% / 99.4% | 95.8% / 98.0% → 95.9% / 98.1% | 96.6% / 95.6% → 96.5% / 95.6% | 96.7% / 88.8% → 96.7% / 88.6% |
| SPY | 97.2% / 99.4% → 97.2% / 99.3% | 94.7% / 95.5% → 94.7% / 95.4% | 96.7% / 95.2% → 96.7% / 95.2% | 98.0% / 92.5% → 98.0% / 92.3% |
| SPX | 93.4% / 98.8% → 94.8% / 99.0% | 91.9% / 97.5% → 91.9% / 97.4% | 93.3% / 94.8% → 93.5% / 95.0% | 96.2% / 90.5% → 95.0% / 87.8% |

### QQQ (NQ pts; 6397 crossings; 0.59 s)

Spearman |error| vs trailing 15-min volatility 0.293 (vs 5-min 0.29); day p95 vs day volatility 0.313.

| Day | n | p50 | p95 | max | Day mean 1-min NQ range | Day bias (median) | Bias within 1 tick |
|---|---:|---:|---:|---:|---:|---:|---|
| 2025-10-15 | 172 | 1.03 | 4.57 | 11.2 | 17.74 | +0.145 | yes |
| 2025-10-29 | 310 | 0.94 | 4.54 | 12.45 | 15.77 | -0.101 | yes |
| 2025-11-12 | 166 | 0.61 | 3.52 | 8.08 | 15.94 | +0.167 | yes |
| 2025-11-25 | 285 | 1.15 | 5.49 | 11.79 | 17.8 | +0.212 | yes |
| 2025-12-10 | 214 | 0.83 | 4.59 | 9.44 | 16.04 | +0.065 | yes |
| 2026-01-08 | 221 | 0.69 | 3.24 | 10.18 | 13.66 | +0.045 | yes |
| 2026-01-22 | 190 | 1.18 | 3.82 | 15.68 | 12.94 | +0.298 | no |
| 2026-02-05 | 433 | 1.67 | 7.12 | 13.65 | 28.77 | +0.036 | yes |
| 2026-02-24 | 284 | 0.75 | 3.31 | 7.01 | 13.98 | -0.051 | yes |
| 2026-03-06 | 361 | 1.33 | 5.59 | 22.47 | 23.92 | -0.055 | yes |
| 2026-03-11 | 262 | 1.19 | 4.58 | 10.34 | 18.56 | +0.226 | yes |
| 2026-03-17 | 183 | 0.69 | 2.68 | 4.16 | 11.91 | +0.054 | yes |
| 2026-03-18 | 207 | 0.61 | 2.08 | 3.83 | 16.13 | +0.000 | yes |
| 2026-03-19 | 407 | 0.7 | 2.65 | 8.78 | 21.63 | +0.086 | yes |
| 2026-03-20 | 99 | 0.61 | 2.67 | 5.43 | 18.9 | +0.089 | yes |
| 2026-03-23 | 348 | 0.82 | 3.47 | 7.21 | 21.77 | +0.023 | yes |
| 2026-03-24 | 392 | 0.88 | 3.47 | 14.27 | 21.51 | -0.017 | yes |
| 2026-03-25 | 256 | 0.74 | 3.03 | 6.5 | 18.36 | -0.118 | yes |
| 2026-03-26 | 313 | 0.63 | 2.75 | 4.95 | 16.54 | +0.041 | yes |
| 2026-03-27 | 394 | 0.59 | 2.31 | 5.22 | 17.13 | -0.046 | yes |
| 2026-03-30 | 508 | 0.63 | 2.69 | 7.61 | 16.83 | +0.030 | yes |
| 2026-03-31 | 392 | 0.86 | 5.42 | 54.59 | 22.46 | -0.106 | yes |

| Trailing-volatility quartile | 15-min mean 1-min range (NQ) | Crossings | p50 | p95 | max | Within fixed 4.0 |
|---|---|---:|---:|---:|---:|---:|
| Q1 | 5.9–13.6 | 1598 | 0.56 | 2.53 | 29.72 | 98.9% |
| Q2 | 13.62–19.12 | 1594 | 0.69 | 3.12 | 11.79 | 97.5% |
| Q3 | 19.13–26.42 | 1604 | 0.95 | 3.91 | 13.67 | 95.4% |
| Q4 | 26.43–64.32 | 1601 | 1.32 | 5.98 | 54.59 | 88.1% |

| # | Date | Time ET | Strike | Error | Trailing vol | Day bias | Within 1 tick |
|---:|---|---|---:|---:|---:|---:|---|
| 1 | 2026-03-31 | 12:44:06 | 575 | -54.59 | 37.72 | -0.106 | yes |
| 2 | 2026-03-31 | 12:43:47 | 575 | -50.98 | 34.12 | -0.106 | yes |
| 3 | 2026-03-31 | 12:43:46 | 575 | -45.23 | 34.12 | -0.106 | yes |
| 4 | 2026-03-31 | 12:43:56 | 575 | -44.07 | 34.12 | -0.106 | yes |
| 5 | 2026-03-31 | 12:44:14 | 575 | -40.11 | 37.72 | -0.106 | yes |
| 6 | 2026-03-31 | 12:43:55 | 575 | -38.82 | 34.12 | -0.106 | yes |
| 7 | 2026-03-31 | 12:44:01 | 575 | -37.57 | 37.72 | -0.106 | yes |
| 8 | 2026-03-31 | 12:44:15 | 575 | -35.83 | 37.72 | -0.106 | yes |
| 9 | 2026-03-31 | 12:44:37 | 575 | -35.61 | 37.72 | -0.106 | yes |
| 10 | 2026-03-31 | 12:44:04 | 575 | -32.57 | 37.72 | -0.106 | yes |

Proposed (not applied): floor = max(2.79, 0.195 × trailing 15-min volatility) NQ pts (minimum = the p95 error in the quietest quartile of months 1–4; k the smallest giving 95% coverage on months 1–4: 95.1% over 1558 crossings); months 5–6 (4839 crossings): 96.5% vs 95.3% for the fixed 4.0, mean floor 4.32.

| Months 5–6 quartile | Crossings | Scaled floor (mean) | Coverage, scaled | Coverage, fixed 4.0 |
|---|---:|---:|---:|---:|
| Q1 | 970 | 2.79 | 97.3% | 99.4% |
| Q2 | 1326 | 3.19 | 95.9% | 98.1% |
| Q3 | 1360 | 4.43 | 96.5% | 95.6% |
| Q4 | 1183 | 6.73 | 96.7% | 88.6% |

### SPY (ES pts; 7379 crossings; 0.59 s)

Spearman |error| vs trailing 15-min volatility 0.198 (vs 5-min 0.208); day p95 vs day volatility 0.546.

| Day | n | p50 | p95 | max | Day mean 1-min ES range | Day bias (median) | Bias within 1 tick |
|---|---:|---:|---:|---:|---:|---:|---|
| 2025-10-15 | 185 | 0.19 | 0.78 | 1.09 | 3.8 | +0.019 | yes |
| 2025-10-29 | 263 | 0.26 | 1.23 | 1.97 | 3.47 | -0.004 | yes |
| 2025-11-12 | 172 | 0.17 | 0.67 | 1.85 | 2.92 | -0.002 | yes |
| 2025-11-25 | 161 | 0.2 | 0.81 | 1.48 | 3.44 | +0.100 | yes |
| 2025-12-10 | 336 | 0.2 | 0.78 | 1.62 | 3.29 | +0.020 | yes |
| 2026-01-08 | 218 | 0.13 | 0.5 | 0.9 | 2.54 | -0.000 | yes |
| 2026-01-22 | 229 | 0.13 | 0.45 | 0.75 | 2.48 | -0.008 | yes |
| 2026-02-05 | 556 | 0.25 | 0.94 | 2.18 | 5.62 | -0.005 | yes |
| 2026-02-24 | 222 | 0.15 | 0.7 | 1.57 | 2.86 | +0.004 | yes |
| 2026-03-06 | 402 | 0.21 | 0.95 | 2.19 | 5.45 | +0.005 | yes |
| 2026-03-11 | 301 | 0.23 | 0.9 | 1.7 | 4.22 | -0.047 | yes |
| 2026-03-17 | 284 | 0.15 | 0.45 | 1.06 | 2.89 | +0.000 | yes |
| 2026-03-18 | 344 | 0.18 | 0.67 | 1.59 | 3.84 | -0.016 | yes |
| 2026-03-19 | 622 | 0.2 | 0.75 | 1.89 | 4.97 | -0.012 | yes |
| 2026-03-20 | 428 | 0.19 | 0.65 | 2.24 | 4.51 | -0.006 | yes |
| 2026-03-23 | 521 | 0.18 | 0.73 | 4.89 | 5.19 | -0.008 | yes |
| 2026-03-24 | 396 | 0.19 | 0.79 | 2.1 | 4.94 | -0.009 | yes |
| 2026-03-25 | 480 | 0.16 | 0.73 | 1.55 | 4.24 | +0.034 | yes |
| 2026-03-26 | 379 | 0.13 | 0.53 | 1.57 | 3.7 | +0.011 | yes |
| 2026-03-27 | 215 | 0.15 | 0.45 | 0.93 | 4.02 | -0.032 | yes |
| 2026-03-30 | 319 | 0.15 | 0.65 | 4.75 | 3.83 | -0.008 | yes |
| 2026-03-31 | 346 | 0.21 | 2.15 | 16.06 | 5.15 | -0.000 | yes |

| Trailing-volatility quartile | 15-min mean 1-min range (ES) | Crossings | p50 | p95 | max | Within fixed 0.78 |
|---|---|---:|---:|---:|---:|---:|
| Q1 | 1.33–3.15 | 1842 | 0.14 | 0.52 | 1.85 | 98.7% |
| Q2 | 3.17–4.17 | 1837 | 0.18 | 0.78 | 4.75 | 95.0% |
| Q3 | 4.18–5.53 | 1847 | 0.2 | 0.76 | 5.93 | 95.3% |
| Q4 | 5.55–14.25 | 1853 | 0.23 | 1.0 | 16.06 | 91.3% |

| # | Date | Time ET | Strike | Error | Trailing vol | Day bias | Within 1 tick |
|---:|---|---|---:|---:|---:|---:|---|
| 1 | 2026-03-31 | 12:41:30 | 646 | +16.06 | 5.98 | -0.000 | yes |
| 2 | 2026-03-31 | 12:41:31 | 646 | +15.81 | 5.98 | -0.000 | yes |
| 3 | 2026-03-31 | 12:41:24 | 646 | +13.06 | 5.98 | -0.000 | yes |
| 4 | 2026-03-31 | 12:41:23 | 646 | +12.56 | 5.98 | -0.000 | yes |
| 5 | 2026-03-31 | 12:41:20 | 646 | +11.31 | 5.98 | -0.000 | yes |
| 6 | 2026-03-31 | 12:41:12 | 646 | +10.31 | 5.98 | -0.000 | yes |
| 7 | 2026-03-31 | 12:47:05 | 646 | +7.58 | 11.95 | -0.000 | yes |
| 8 | 2026-03-31 | 12:47:02 | 646 | +7.33 | 11.95 | -0.000 | yes |
| 9 | 2026-03-31 | 12:40:10 | 645 | +5.93 | 4.82 | -0.000 | yes |
| 10 | 2026-03-31 | 12:40:38 | 646 | -5.42 | 4.82 | -0.000 | yes |

Proposed (not applied): floor = max(0.53, 0.2 × trailing 15-min volatility) ES pts (minimum = the p95 error in the quietest quartile of months 1–4; k the smallest giving 95% coverage on months 1–4: 95.0% over 1564 crossings); months 5–6 (5815 crossings): 96.6% vs 95.2% for the fixed 0.78, mean floor 0.94.

| Months 5–6 quartile | Crossings | Scaled floor (mean) | Coverage, scaled | Coverage, fixed 0.78 |
|---|---:|---:|---:|---:|
| Q1 | 1038 | 0.56 | 97.2% | 99.3% |
| Q2 | 1491 | 0.74 | 94.7% | 95.4% |
| Q3 | 1667 | 0.96 | 96.7% | 95.2% |
| Q4 | 1619 | 1.34 | 98.0% | 92.3% |

### SPX (ES pts; 6428 crossings; 0.59 s)

Spearman |error| vs trailing 15-min volatility 0.236 (vs 5-min 0.236); day p95 vs day volatility 0.829.

| Day | n | p50 | p95 | max | Day mean 1-min ES range | Day bias (median) | Bias within 1 tick |
|---|---:|---:|---:|---:|---:|---:|---|
| 2025-10-15 | 210 | 0.21 | 0.62 | 1.49 | 3.8 | +0.020 | yes |
| 2025-10-29 | 114 | 0.19 | 0.89 | 1.93 | 3.47 | -0.010 | yes |
| 2025-11-12 | 252 | 0.16 | 0.55 | 1.76 | 2.92 | +0.010 | yes |
| 2025-11-25 | 535 | 0.22 | 0.75 | 2.02 | 3.44 | -0.001 | yes |
| 2025-12-10 | 364 | 0.2 | 0.81 | 5.7 | 3.29 | -0.010 | yes |
| 2026-01-08 | 329 | 0.17 | 0.51 | 1.01 | 2.54 | +0.032 | yes |
| 2026-01-22 | 299 | 0.16 | 0.6 | 1.86 | 2.48 | +0.020 | yes |
| 2026-02-05 | 215 | 0.24 | 1.02 | 2.31 | 5.62 | +0.011 | yes |
| 2026-02-24 | 401 | 0.17 | 0.59 | 1.11 | 2.86 | -0.010 | yes |
| 2026-03-06 | 352 | 0.31 | 0.91 | 1.97 | 5.45 | +0.025 | yes |
| 2026-03-11 | 589 | 0.25 | 0.8 | 1.5 | 4.22 | +0.000 | yes |
| 2026-03-17 | 152 | 0.14 | 0.57 | 1.35 | 2.89 | +0.005 | yes |
| 2026-03-18 | 410 | 0.22 | 0.64 | 1.35 | 3.84 | +0.016 | yes |
| 2026-03-19 | 303 | 0.28 | 0.85 | 1.53 | 4.97 | -0.000 | yes |
| 2026-03-20 | 120 | 0.25 | 0.82 | 1.15 | 4.51 | +0.030 | yes |
| 2026-03-23 | 212 | 0.26 | 1.07 | 1.58 | 5.19 | -0.004 | yes |
| 2026-03-24 | 251 | 0.24 | 0.82 | 1.76 | 4.94 | +0.040 | yes |
| 2026-03-25 | 427 | 0.2 | 0.82 | 4.49 | 4.24 | -0.010 | yes |
| 2026-03-26 | 278 | 0.18 | 0.58 | 1.11 | 3.7 | -0.040 | yes |
| 2026-03-27 | 233 | 0.2 | 0.79 | 2.7 | 4.02 | -0.015 | yes |
| 2026-03-30 | 305 | 0.21 | 0.83 | 5.23 | 3.83 | +0.018 | yes |
| 2026-03-31 | 77 | 0.26 | 1.0 | 4.91 | 5.15 | -0.049 | yes |

| Trailing-volatility quartile | 15-min mean 1-min range (ES) | Crossings | p50 | p95 | max | Within fixed 0.78 |
|---|---|---:|---:|---:|---:|---:|
| Q1 | 1.2–2.8 | 1597 | 0.16 | 0.52 | 4.49 | 98.7% |
| Q2 | 2.82–3.73 | 1606 | 0.19 | 0.67 | 5.23 | 97.4% |
| Q3 | 3.75–5.0 | 1613 | 0.23 | 0.76 | 2.61 | 95.6% |
| Q4 | 5.02–13.38 | 1612 | 0.29 | 1.01 | 5.7 | 88.5% |

| # | Date | Time ET | Strike | Error | Trailing vol | Day bias | Within 1 tick |
|---:|---|---|---:|---:|---:|---:|---|
| 1 | 2025-12-10 | 14:40:07 | 6865 | -5.70 | 7.43 | -0.010 | yes |
| 2 | 2026-03-30 | 09:32:11 | 6425 | -5.23 | 3.18 | +0.018 | yes |
| 3 | 2026-03-31 | 09:32:18 | 6400 | -4.91 | 3.07 | -0.049 | yes |
| 4 | 2026-03-25 | 14:55:33 | 6595 | -4.49 | 2.07 | -0.010 | yes |
| 5 | 2026-03-25 | 13:02:09 | 6600 | +3.38 | 3.5 | -0.010 | yes |
| 6 | 2026-03-25 | 13:02:04 | 6605 | -3.06 | 3.5 | -0.010 | yes |
| 7 | 2026-03-25 | 13:43:27 | 6615 | +2.93 | 3.05 | -0.010 | yes |
| 8 | 2026-03-30 | 13:47:20 | 6365 | -2.77 | 3.22 | +0.018 | yes |
| 9 | 2026-03-27 | 14:43:33 | 6390 | -2.70 | 2.73 | -0.015 | yes |
| 10 | 2026-03-25 | 13:52:02 | 6610 | -2.61 | 3.92 | -0.010 | yes |

Proposed (not applied): floor = max(0.52, 0.165 × trailing 15-min volatility) ES pts (minimum = the p95 error in the quietest quartile of months 1–4; k the smallest giving 95% coverage on months 1–4: 95.0% over 2103 crossings); months 5–6 (4325 crossings): 93.7% vs 94.2% for the fixed 0.78, mean floor 0.74.

| Months 5–6 quartile | Crossings | Scaled floor (mean) | Coverage, scaled | Coverage, fixed 0.78 |
|---|---:|---:|---:|---:|
| Q1 | 580 | 0.52 | 94.8% | 99.0% |
| Q2 | 1251 | 0.55 | 91.9% | 97.4% |
| Q3 | 1285 | 0.72 | 93.5% | 95.0% |
| Q4 | 1209 | 1.06 | 95.0% | 87.8% |

- **The worst crossings are one event:** 2026-03-31 12:40–12:47 ET. NQ rallied about 150 pts in two minutes and fell back while QQQ and SPY spot kept rising until 12:44:30, so R itself moved about 0.5% in two minutes (41.55 → 41.31). It is a fast-market ETF-vs-futures dislocation, not a data glitch; the day's bias stayed ≈ 0. No floor of this kind covers it (the scaled floor gives about 7 NQ there).
- **Verdict (0.59 s):** the error grows with trailing volatility, but moderately (Spearman QQQ 0.29, SPY 0.20, SPX 0.24; p95 from the quietest to the busiest quartile QQQ 2.5 → 6.0 NQ, SPY 0.52 → 1.0 ES, SPX 0.52 → 1.01 ES). The fixed floor over-covers quiet periods and under-covers busy ones (QQQ months 5–6: 99.4% vs 88.6%); the scaled floor keeps QQQ and SPY coverage about flat (96–98%). SPX's scaled floor generalizes worse (93.7% on months 5–6, 91.9–95.0% per quartile), and SPX creates no NQ stops anyway. The 0.59 s delay barely moves QQQ/SPY; SPX's minimum rises 0.45 → 0.52 ES. Proposed for step 13 (§13 Q13-vol), not applied.
- The 0 s column above uses the same board-in-force code with the delay set to 0, so it differs slightly from the 10-08 study (which took the board before the crossing pair): QQQ minimum 2.75 there, 2.77 here.

## R11. The gate commands on the 22 development days (2026-10-08)

**Re-run 2026-10-11 with §5.2's relevance rule (owner 2026-10-11; these numbers replace the 10-09 ones below).** A node becomes relevant at ≥ 25% of its own board's King and stays relevant until it is < 20% (`node_drop_pct`), tracked by (map, strike), fresh each session, a strike missing from a board keeping its state (feature_spec F2.4). One function, `features/nodes.py` `relevance`, now drives the gate's crossings (cache version 3), the F1.6 precision diagnostic and the Atlas tables (listing and each crossing: a crossing counts only while its node is still relevant on the board in force then); step 11 uses the same function. `recorded-day` and `ndxp-select` score NDXP at every board, so the rule does not enter their main numbers; a report-only check at NDXP's relevant-node crossings was added (R14, 10-09: 368 crossings, QQQ × k N = 10 p95 4.90 vs 5.13 at every board; basis + carry 15.79 vs 16.49). The refactored crossing code reproduces the cached crossings exactly (2026-03-20, all three chains). Frozen values not changed: `stop_buffer_floor` QQQ 4.0 NQ and `atlas_match_tol` 4.0 NQ / 0.78 ES (the owner decides).

| Chain | Crossings | (a) bias, median [95% day-block interval] | (b) chosen p95 vs best (limit ×1.10) | Pass |
|---|---|---|---|---|
| QQQ | 6,397 → **6,671** | +0.022 [−0.017, +0.058] → **+0.028 [−0.011, +0.065]** NQ | 4.010 vs 4.040 → **4.015 vs 4.046** NQ (4.450) | all three |
| SPY | 7,379 → **7,594** | 0.000 [−0.005, +0.005] → **0.000 [−0.005, +0.005]** ES | 0.775 vs 0.791 → **0.792 vs 0.798** ES (0.878) | all three |
| SPX | 6,428 → **6,951** | +0.001 [−0.008, +0.010] → **+0.008 [−0.002, +0.014]** ES | 0.779 vs 0.781 → **0.779 vs 0.785** ES (0.863) | all three |

Against the frozen values: QQQ's p95 4.015 is 0.4% above the 4.0 NQ stop floor and SPY's 0.792 is 1.5% above its 0.78 ES Atlas tolerance (shown to the owner, nothing changed). Alignment does not use relevance (unchanged). Atlas regression check, pooled over the five sample days: QQQ 94.2% of 500 → **94.6% of 519**, SPY 95.2% of 495 → **94.6% of 574** (no alert). Per day, QQQ / SPY crossings and share: 03-06 145 → 154 (89.7 → 90.3%) / 114 → 114 (97.4%); 03-11 67 → 65 (91.0 → 90.8%) / 86 → 98 (95.4 → 90.8%); 03-18 49 → 49 (100%) / 107 → 107 (92.5%); 03-24 142 → 155 (95.1 → 95.5%) / 84 → 151 (92.9 → 94.7%); 03-26 97 → 96 (99.0 → 100%) / 104 → 104 (97.1%). Held nodes listed (marked "held", % rounded down): QQQ 15 node rows with 23 crossings, SPY 16 with 82; SPY strike 651 on 03-24 at 09:35 (24.99% of King) is back, held, with 20 crossings. The few QQQ crossings that drop out (03-11, 03-26) came after the node had fallen below 20%.

`nqbot step8b bias|estimator|alignment --dates <the 22 days of R1>`; QQQ, SPY, SPX (NDXP pending); event-moment crossings with the approved ingest rule and estimators. Reports in `<reports_dir>/step8b/`.

**Re-run 2026-10-09 with `map_latency_sec` 0.59 (the numbers below replace the 10-08 run).** The fix: the 10-08 gate took each crossing's R (and its relevant nodes) from the board just before the crossing pair, whatever the delay, so `map_latency_sec` never entered it. A crossing now uses the board in force at t*: the last board applied by then, at floor(`asOf`) + 1 s + `map_latency_sec` (history; live max(arrival, that)), the same rule as the Atlas tables and `recorded-day` (`mapping.apply_times_ns` / `in_force_index`; gate cache version 2, keyed on the latency). QQQ and SPY barely move; SPX moves most, since its per-board R is now ≈ 1.6 s old at a crossing: p95 0.731 → **0.779** ES vs best 0.781 (limit 1.10 × 0.781 = 0.859: passes), bias +0.010 [0.000, +0.018] → **+0.001 [−0.008, +0.010]**. Alignment does not use the latency (12 of the 22 days re-run, all identical); its 10-08 values stand.

| Chain | (a) bias, median [95% day-block interval] | (b) chosen p95 vs best possible R (limit ×1.10) | Alignment: latest fitted content (max +1000 ms) | Pass |
|---|---|---|---|---|
| QQQ (60 s), n 6,397 | +0.022 [−0.017, +0.058] NQ | 4.010 vs 4.040 NQ (4.444) | +860 ms (2025-12-10) | all three |
| SPY (60 s), n 7,379 | 0.000 [−0.005, +0.005] ES | 0.775 vs 0.791 ES (0.870) | +900 ms (2026-01-08) | all three |
| SPX (per board), n 6,428 | +0.001 [−0.008, +0.010] ES | 0.779 vs 0.781 ES (0.859) | −20 ms | all three |

Atlas tables regenerated with 0.59 s: pooled QQQ 94.2% of 500, SPY 95.2% of 495 (0 s: 94.4% of 500, 95.0% of 535). The 40 SPY crossings that drop out are all strike 651 on 2026-03-24 in the 09:35 window: it was exactly 25.0% of King on the 09:34:59 board (in force at 0 s) and just under 25% on the 09:34:58 board (in force at 09:35 once the 0.59 s delay applies), so it is no longer listed; every other strike, minute and count is identical. Timing at the relevance cutoff, not a bug or a lost board. Note: the Atlas listing and the gate's event moments used a plain ≥ 25% per board, not §5.2's `node_drop_pct` (20%) hysteresis, which would have kept 651 (fixed 2026-10-11, above).

10-08 run (`map_latency_sec` not applied), for reference: QQQ +0.022 [−0.018, +0.065], 4.011 vs 4.046; SPY 0.000 [−0.005, +0.005], 0.773 vs 0.791; SPX +0.010 [0.000, +0.018], 0.731 vs 0.783.

- Alignment per day: before 03-17 QQQ +640…+860, SPY +640…+900, SPX −100…−20 ms; from 03-17 QQQ −80…0, SPY −120…−20, SPX −220…−40 ms. None after floor(`asOf`) + 1 s.
- **Atlas tables regenerated** with the approved rule (content-time F, boards applied at floor(`asOf`) + 1 s + `map_latency_sec` (0 until set), F1.7 R in force at each crossing). Share of gated crossings within `atlas_match_tol` (1.0 NQ / 0.5 ES) and within the crossing p95 (4.0 NQ / 0.78 ES):

| Day | QQQ n | QQQ ≤ 1.0 | QQQ ≤ 4.0 | QQQ p95 | SPY n | SPY ≤ 0.5 | SPY ≤ 0.78 | SPY p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 03-06 | 145 | 42% | 90% | 5.46 | 114 | 92% | 97% | 0.58 |
| 03-11 | 67 | 37% | 91% | 4.62 | 86 | 86% | 95% | 0.67 |
| 03-18 | 49 | 53% | 100% | 1.98 | 107 | 83% | 93% | 0.91 |
| 03-24 | 142 | 59% | 96% | 3.78 | 124 | 85% | 93% | 0.93 |
| 03-26 | 97 | 69% | 99% | 2.45 | 104 | 94% | 97% | 0.51 |

  `atlas_match_tol` (1.0 NQ / 0.5 ES) sits below the measured crossing floor (findings R7), so no day can have every crossing within it; asked in §13 (Q8b-5).
- **Q8b-5 answered (owner 2026-10-08):** the automated match is information only (a regression check); `atlas_match_tol` frozen at QQQ 4.0 NQ / SPY 0.78 ES, alert below 90% of crossings. Judged on the share pooled over the five sample days (owner 2026-10-08): QQQ 94.2% of 500, SPY 95.2% of 495 with `map_latency_sec` 0.59 (94.4% / 95.0% at 0 s): no alert. Per day, information only (QQQ / SPY): 03-06 89.7% / 97.4%; 03-11 91.0% / 95.4%; 03-18 100% / 92.5%; 03-24 95.8% / 92.7%; 03-26 99.0% / 97.1%. The owner's replay check is the Atlas gate condition.
- **`recorded-day`** checked on the 10-07 recording (cached trades, `--with-prev` = 10-06): content offsets QQQ −120, SPY +300, SPX −20, NDXP −60 ms; NDXP basis + carry p95 13.5 NQ (median +8.4); QQQ × k p95 7.4 / 10.2 / 8.5 / 4.0 for N = 1 / 3 / 5 / 10. 10-07 is not one of the three recordings, so it is left out of the choice (owner: N is not chosen from 10-07).
- **NDXP crossings (owner 2026-10-11, report only):** `recorded-day` also scores each candidate at NDXP's relevant-node crossings (F2.4 on the board in force, the gate's crossing rule, gap window as for every board) and saves them next to the every-board errors; `ndxp-select` shows each candidate's crossing count and p95 next to its every-board held-out p95 and proposes NDXP's stop floor as the larger of the winner's two (the owner decides on Wednesday). The 10-09 file predates this: re-run `recorded-day 2026-10-09` (cached trades and closes, no pull) before `ndxp-select`.
- **NDXP choice (owner 2026-10-08):** `step8b ndxp-select` after the three recordings: candidates basis + carry and QQQ × k N ∈ {5, 10, 20}, leave one day out, pick on two, score on the held-out day, rotate; each candidate's held-out p95 and median; the best held-out p95 wins and is NDXP's stop floor.

## R7. A: is the crossing floor just the 1-second resolution? No.
- The futures' 1-second move at event moments has p95 5.75 NQ (p50 1.25) at QQQ moments, 1.25 ES (p50 0.25) at SPY and SPX moments, and 12.8 NQ / 2.7–3.4 ES in the fastest 1%. The move across the crossing's own board interval has p95 6.5 NQ / 1.25 ES.
- The magnitudes are comparable to the crossing floors (4.04 / 0.77 / 0.78 at event moments). An interpolated crossing could be off by about half an interval's move, so the sizes alone don't decide it.
- **The decisive split** bins crossings by how far the futures moved across that crossing's board interval. Floor p95:

| | futures still (0 ticks) | 1 tick | 2–4 ticks | > 4 ticks | all |
|---|---:|---:|---:|---:|---:|
| QQQ (NQ) | 3.29 (n 376) | 4.04 | 3.18 | 4.44 | 4.04 |
| SPY (ES) | 0.62 (n 1,541) | 0.69 | 0.74 | 1.18 | 0.77 |
| SPX (ES) | 0.53 (n 1,388) | 0.63 | 0.83 | 1.29 | 0.78 |

- With the futures completely still, 81% (QQQ), 80% (SPY) and 68% (SPX) of the floor remains. **Timing explains only the part above the "still" column.** The rest is real: at the moment the ETF or index spot prints the strike, the futures sit up to about 3.3 NQ / 0.6 ES (p95) away from strike × R, even in a still market. That is the instantaneous gap between the underlying's printed spot and the futures, which no R can remove. The level a strike stands for is fuzzy by that much in futures terms.
- So under the owner's rule (c), the stop floor uses the **crossing p95**, not the level error.

## R8. B: level error
strike × |R used − best R| at event moments (best R = a centered 120-s median, evaluation only), p95 (p50) and bias [95% day-block interval]:

| Chain | per board, aligned | 15 s | 30 s | 60 s | 120 s | 300 s |
|---|---|---|---|---|---|---|
| QQQ (NQ) | 4.70 (0.77) | 1.52 | 0.86 | **0.55** (0.13), bias −0.003 [−0.009, 0.000] | 0.59 | 0.73 |
| SPY (ES) | 0.89 (0.16) | 0.26 | 0.16 | **0.11** (0.03), bias 0.000 [0.000, 0.000] | 0.11 | 0.15 |
| SPX (ES) | 0.64 (0.19), bias −0.009 [−0.020, 0.000] | 0.43 | 0.34 | 0.26 | 0.31 | 0.38 |

- **SPY window (E):** the lowest event-moment level error over all moves together is **60 s** (0.107, vs 0.157 for 30 s, 0.256 for 15 s and 0.893 per board). In the fastest 1% it is also the lowest of the four (3.08 vs 6.18 per board).
- The level error is much smaller than the crossing p95 because both the estimate and the reference average out the same spot noise. It measures how far the estimate sits from a smoothed R, not where the futures actually are when the strike prints (R7).

## R9. C: NDXP, QQQ × k vs basis + carry
- **Sources (free):**
  - QQQ official closes and ex-dividend dates: Nasdaq's public quote API (`api.nasdaq.com/api/quote/QQQ/historical`, `/dividends`; QQQ lists on Nasdaq, so this is the closing-cross price). Its 10-06 close, 759.66, equals the stream's QQQ `previousClose` exactly.
  - NDX closes: FRED `NASDAQ100`.
  - Cached in `<root>/data/raw/ndx/`. Cost $0. (Stooq answered with a bot check and was not used.)
- **Method:** k = NDX close ÷ QQQ close, averaged over the previous N sessions. Every QQQ close before an ex-dividend date ≤ the day is moved to the ex basis (close − the declared dividend), so k is continuous across ex-dates. Known before the open. NDX̂(t) = QQQ spot(t) × k, so an NDXP strike K maps to K × F(t) ÷ (QQQ(t) × k), i.e. R_NDXP = R_QQQ ÷ k.
- **Day-ahead, development months (125 days; NDX̂ at the close vs NDX, pts):**
  - N = 1: p50 6.5, p95 16.1
  - N = 3: p50 4.7, p95 14.1
  - **N = 5: p50 5.4, p95 13.8, signed median +2.2**
  - N = 10: p50 6.2, p95 14.3
  - Basis + carry: p50 4.7, p95 15.1, +0.2
  - Ex-date days (12-22, 03-23): QQQ × k N = 5 errs +1.7 and −12.5.
- **Live 2026-10-07, against the stream's real NDX (NQ pts at a strike = NDX), recordings 10:01 / 14:59:**

| Method | Signed median | p95 |
|---|---|---|
| Basis + carry | +12.4 / +8.0 | 14.4 / 10.0 |
| QQQ × k, N = 1 | +5.8 / +4.4 | 7.9 / 6.3 |
| QQQ × k, N = 3 | +8.7 / +7.3 | 10.8 / 9.1 |
| QQQ × k, N = 5 | +7.0 / +5.5 | 9.1 / 7.4 |
| QQQ × k, N = 10 | +2.5 / +1.0 | 4.6 / 2.9 |

The per-board QQQ spot and the approved 60-s QQQ R give the same result to within 0.1.
- **Reading:** both methods carry a day-to-day drift of about 5 pts typical and about 14–15 at p95. QQQ × k was better on the one live day for every N, but one day is one draw of that drift. **Not chosen yet.** The NDXP recordings decide, and they need QQQ in them (R10).

## R10. D–F, and what runs next
- **D (ingest, approved in principle; written into §5.1/F1 when the final numbers are approved):** one rule for every era and live: apply a board at max(arrival, floor(`asOf`) + 1 s + `map_latency_sec`), and read the futures at the board's measured content time. History has no arrival, so it uses floor(`asOf`) + 1 s + `map_latency_sec`.
  - Content offsets from the whole second of `asOf`, medians from R4: before 03-17, QQQ +740, SPY +840, SPX −80, NDXP +1000 ms; from 03-17, QQQ −60, SPY −60, SPX −80, NDXP +1000 (0 on 03-24–27); live 10-07, QQQ −110, SPX −60, NDXP −40, SPY +420.
  - No measured content time is past floor(`asOf`) + 1 s. NDXP's sits exactly at +1 s, and its correlation peak is one step wide.
  - **Alert rule:** an alignment check fits each recording's offset per map against the futures and alerts if the fitted content time, or the upper edge of its peak, falls after floor(`asOf`) + 1 s. To be built as `step8b alignment` (needs the futures for each recorded day).
  - The NDXP-only recordings can't measure SPY. SPY is re-measured on the next recording that includes it.
- **E (R estimator, approved in principle):** QQQ trailing 60-s median; SPY trailing 60-s median (R8); SPX per board (aligned) with F1.2.
  - Trailing only, over applied boards by content time. Reset at the session's first applied board (09:30, or after `ratio_open_skip_sec` for SPX/NDXP), at a contract change (never inside a session, since the contract is fixed per session, so the session reset covers a roll) and after a board gap > `ratio_gap_reset_sec` (10 s in the study).
  - **Warm-up:** right after a reset the median uses whatever applied boards exist since it (the first board alone at first), with no minimum count. The study's open and roll-day rows show the cost of that is small.
  - The same function in backtest, restart rebuilds and live.
- **F (gate proposal):**
  - (a) Bias: every chain passes. All intervals contain 0 or sit within one futures tick (largest: QQQ crossing +0.06 NQ before 03-17).
  - (b) Within 10% of the best possible R: QQQ 4.00 vs 4.04, SPY 0.78 vs 0.77, SPX 0.74 vs 0.78. All pass.
  - (c) Stop floor per level source, from the crossing p95 (A did not confirm timing): QQQ-edge stops 4.0 NQ; NDXP-edge stops ≈ 14–15 NQ, pending the method; chart levels 0; SPY/SPX create no NQ stops.
  - Node bands against their chain's error: QQQ ±0.5 $ = ±20.7 NQ ≥ 4.0; SPY ±0.5 $ = ±5.0 ES ≥ 0.78; SPX ±5 ≥ 0.74; NDXP ±20 ≥ ≈ 15 (tight).
  - F1.6 stays a diagnostic; the spot-lag condition becomes the alignment check. The owner's Atlas replay check stays.
- **NDXP recordings (approved, NDXP only):** `nqbot step7 stream-session --maps NDXP` (new; 15-minute part files, no Skylit lock, about 395 credits per full session, printed first). Windows task `nqbot NDXP session recording` starts it at 09:27 ET on 2026-10-09, 10-12 and 10-13; log in `<root>/logs/stream_session_<date>.txt`.
  - **Gap:** NDXP-only recordings can't test QQQ × k, which needs QQQ's spot at the same moments. Adding QQQ costs about 395 more credits per session.
  - Both methods also need the NQ trades of each recorded day ($0, included) to be scored. Both need the owner's OK.

## R1. Round-2 method (owner 2026-10-07)
- **Days:** 22 development days, 11 per stamping era. Before 2026-03-17: 2025-10-15, 10-29, 11-12, 11-25, 12-10, 2026-01-08, 01-22, 02-05, 02-24, 03-06, 03-11. From 2026-03-17: 03-17, 18, 19, 20, 23, 24, 25, 26, 27, 30, 31.
- **Alignment:** a fixed rule per era and chain (not fitted per day). F is read at floor(`asOf`) + δ, with δ in ms: before 03-17, QQQ/SPY +680 and SPX −105; from 03-17, QQQ −70, SPY −55, SPX −80. These come from the five sample days. The out-of-sample check is in R4.
- **No circularity:** a crossing between boards a0 and a1 is mapped with the R known *before* a0. For per-board R that is the board before a0 (with F1.2 in force). For a trailing median it is the window ending at that board. The board where the crossing starts never contributes its own R.
- **Event moments:** crossings of strikes that are relevant nodes (≥ 25% of King) on that earlier board. At such a moment the futures price sits at the node, which is where a touch event would fire. The mapped distance is deliberately not used as a filter, because that would cut off the errors being measured. These are split into normal moves and the fastest 1% (largest 60-s futures move, pooled per chain).
- **Bias:** the median signed error (futures − strike × R) per chain and era. The 95% interval comes from a day-block bootstrap (2,000 resamples of days).
- **Floor:** the best possible R, a centered median of the aligned per-board R over ±60 s around the crossing. It uses future data and is for evaluation only.
- **Units:** QQQ in NQ pts, SPY/SPX in ES pts. Underlying ticks use the median R: QQQ 41.39 (1¢ = 0.414 NQ), SPY 10.09 (1¢ = 0.101 ES), SPX 1.006 (0.01 = 0.010 ES).

## R2. The table (p95 unless marked; bias = median signed error [95% day-block interval])

| Chain | Estimator | All crossings | Event, normal | Event, fastest 1% | p95 before / from 03-17 | Bias before 03-17 | Bias from 03-17 |
|---|---|---:|---:|---:|---|---|---|
| QQQ | per board, as built | 5.42 | 5.14 | 10.20 | 5.93 / 4.90 | +0.015 [−0.077, +0.089] | +0.007 [0.000, +0.079] |
| QQQ | per board, aligned | 4.83 | 4.63 | 7.20 | 5.71 / 3.90 | +0.064 [0.000, +0.121] | +0.016 [−0.007, +0.085] |
| QQQ | trailing 15 s | 4.21 | 4.12 | **5.95** | 5.16 / 3.19 | +0.079 [+0.023, +0.139] | +0.020 [0.000, +0.065] |
| QQQ | trailing 30 s | 4.08 | 3.99 | 7.90 | 5.15 / 3.25 | +0.073 [+0.036, +0.096] | +0.017 [−0.006, +0.047] |
| QQQ | **trailing 60 s** | **4.07** | **3.94** | 7.08 | 5.10 / 3.28 | +0.056 [+0.009, +0.089] | +0.012 [−0.016, +0.040] |
| QQQ | trailing 120 s | 4.09 | 3.98 | 7.19 | 5.04 / 3.27 | +0.038 [+0.008, +0.084] | +0.023 [−0.014, +0.050] |
| QQQ | trailing 300 s | 4.11 | 4.03 | 7.64 | 5.09 / 3.32 | +0.057 [+0.022, +0.089] | +0.014 [−0.028, +0.055] |
| QQQ | *floor* | 4.08 | 4.00 | 7.06 | 5.08 / 3.17 | +0.045 [0.000, +0.081] | +0.013 [−0.007, +0.052] |
| SPY | per board, as built | 1.17 | 1.07 | 2.55 | 1.20 / 1.16 | +0.002 [−0.002, +0.047] | +0.005 [0.000, +0.036] |
| SPY | per board, aligned | 0.97 | 0.90 | **2.70** | 1.12 / 0.89 | +0.002 [0.000, +0.044] | 0.000 [−0.005, +0.002] |
| SPY | trailing 15 s | 0.83 | 0.75 | 5.89 | 0.95 / 0.76 | 0.000 [−0.002, +0.005] | 0.000 [0.000, +0.006] |
| SPY | trailing 30 s | **0.82** | 0.75 | 10.71 | 0.91 / 0.76 | +0.005 [0.000, +0.020] | 0.000 [−0.003, +0.006] |
| SPY | **trailing 60 s** | 0.82 | **0.75** | 5.57 | 0.90 / 0.76 | +0.002 [−0.002, +0.016] | 0.000 [−0.005, +0.006] |
| SPY | trailing 120 s | 0.83 | 0.75 | 5.72 | 0.90 / 0.76 | +0.001 [−0.004, +0.010] | 0.000 [−0.005, +0.009] |
| SPY | trailing 300 s | 0.82 | 0.76 | 3.38 | 0.91 / 0.76 | 0.000 [−0.006, +0.010] | 0.000 [−0.010, +0.010] |
| SPY | *floor* | 0.82 | 0.75 | 5.57 | 0.90 / 0.76 | +0.001 [−0.004, +0.005] | 0.000 [−0.006, +0.003] |
| SPX | per board, as built | 0.83 | 0.73 | 1.63 | 0.74 / 0.93 | +0.009 [−0.001, +0.011] | +0.010 [0.000, +0.018] |
| SPX | **per board, aligned** | **0.82** | **0.72** | **1.47** | 0.75 / 0.91 | +0.010 [0.000, +0.018] | +0.008 [0.000, +0.010] |
| SPX | trailing 15 s | 0.87 | 0.75 | 2.28 | 0.78 / 0.96 | 0.000 [−0.003, +0.009] | +0.003 [−0.007, +0.011] |
| SPX | trailing 30 s | 0.92 | 0.79 | 2.60 | 0.82 / 1.01 | −0.001 [−0.009, +0.009] | +0.005 [−0.008, +0.015] |
| SPX | trailing 60 s | 0.94 | 0.83 | 2.72 | 0.86 / 1.04 | 0.000 [−0.011, +0.010] | +0.004 [−0.007, +0.020] |
| SPX | trailing 120 s | 0.97 | 0.85 | 2.64 | 0.89 / 1.06 | −0.001 [−0.012, +0.013] | +0.003 [−0.012, +0.021] |
| SPX | trailing 300 s | 1.00 | 0.87 | 2.63 | 0.92 / 1.08 | +0.001 [−0.019, +0.019] | +0.009 [−0.008, +0.028] |
| SPX | *floor* | 0.89 | 0.76 | 2.72 | 0.80 / 0.98 | +0.001 [−0.005, +0.009] | −0.006 [−0.017, +0.006] |
| NDXP | basis + carry, known before the open (R6) | 15.1 (\|day-ahead error\|, 124 days) | — | — | — | +0.22 (signed median, all 124 days) | — |
| NDXP | as built (spot is NQ, R ≈ 1) | 224 | — | — | — | −128 | — |

Crossings counted: QQQ 9,988 (6,400 at event moments, 65 in the fastest 1%), SPY 10,332 (7,374; 75), SPX 15,599 (6,428; 69). The fastest-1% column rests on 65–75 crossings, so it is noisy.

## R3. What the table says
1. **Removing the circularity raises every number a little,** and the order of the estimators is unchanged. Alignment still gives the biggest single gain on QQQ/SPY (QQQ 5.42 → 4.83, SPY 1.17 → 0.97).
2. **QQQ:** a trailing 60-s median reaches the floor (4.07 vs 4.08 overall, 3.94 vs 4.00 at event moments). 30 s and 120 s are within 0.02. 15 s is the best in the fastest 1% (5.95 vs 7.08), on 65 crossings.
3. **SPY:** every window from 15 s to 300 s reaches the floor in normal moves (0.75 vs 0.90 per board). In the fastest 1% the per-board R does better (2.70 vs 5.6–10.7), on 75 crossings. ES is only read across markets, never traded (F0.11).
4. **SPX:** the per-board aligned R beats every window and the centered floor (0.82 overall, 1.47 in the fastest 1%). The index already moves in 0.01 steps with no tick noise, and smoothing only adds lag.
5. **Era:** QQQ's p95 is much higher before 03-17 (5.10 vs 3.28 with 60 s). Before 03-17 the ETF spot was a coarser last trade (half-cent prints 7–11% vs ≤ 2.3% on the sample days) with a vaguer moment in time (correlation peak r 0.35–0.64 vs 0.46–0.72).
6. **Bias is practically zero:** every median is ≤ 0.08 NQ pt (0.2 of a QQQ cent) and ≤ 0.01 ES pt. Before 03-17, QQQ's intervals for the windows exclude 0 by +0.04–0.08 NQ pts. Part of that is the alignment rule used here: its +680 ms is earlier than the 22-day medians in R4 (QQQ +740, SPY +840).
7. **Recommended:** **QQQ trailing 60 s, SPY trailing 60 s, SPX per board (aligned), NDXP per R6.** These are the lowest or tied-lowest totals. One window for both ETFs keeps a single parameter. The owner may prefer per-board R for SPY because of its fast-move tail.

## R4. Timestamps across all 22 days, and live (item 4)
Measured content offset (ms from the whole second of `asOf`; median [range], 11 days per era):

| | QQQ | SPY | SPX | NDXP |
|---|---|---|---|---|
| History before 2026-03-17 | +740 [+640, +860] | +840 [+640, +900] | −80 [−100, −20] | **+1000** [+1000, +1000] (r ≈ 0.9997) |
| History from 2026-03-17 | −60 [−80, 0] | −60 [−120, −20] | −80 [−220, −40] | +1000 on 7 days, 0 on 03-24–27 |
| Live stream 2026-10-07 (10:01 / 14:59) | −80 / −140 | +520 / +320 (arrival − `asOf` p50 526 / 413 ms) | −100 / −20 | 0 / −80 |

- The pre-03-17 rule holds on the nine new days. QQQ/SPY content is late in the stamped second, never past +0.9 s, and NDXP's is exactly +1 s (its spot equals the NQ trade one second after `asOf` on every pre-era day).
- In live, nothing is newer than its arrival, and live applies a board at max(arrival, `asOf` + `map_latency_sec`), so live has no lookahead. Live SPY content (+0.3/+0.5 s) differs from post-03-17 history (−0.06 s). **Parity (`step7 parity --session 2026-10-07`, run 2026-10-08 08:50):** 3,585 boards matched, none revised, none only in the stream. History keeps millisecond `asOf` for 10-07, and the stream's whole-second `asOf` is that value cut to the second, so a live board holds the same content as post-03-17 history. QQQ/SPX/NDXP live offsets agree with that era. SPY on 10-07 sits later (+0.3/+0.5 s) than SPY in March (−0.06 s), so SPY's offset has drifted and is re-measured on more days before its value is set.
- **Proposed ingest rule (Q8b-2), so no board is applied before the latest market data it holds:**
  - Each board gets a **content time** c = floor(`asOf`) + `board_content_offset_ms`[era][chain] (the measured median), used to read F for R.
  - It also gets a **latest-data time** u = floor(`asOf`) + `board_content_max_ms`[era][chain] (the measured upper edge, rounded up to the end of the stamped second): before 03-17, QQQ/SPY/NDXP +1000 ms and SPX 0; from 03-17, QQQ/SPY/SPX 0 and NDXP +1000 ms.
  - History applies the board at max(`asOf`, u) + `map_latency_sec`. Live applies it at max(arrival, `asOf` + `map_latency_sec`), unchanged.
  - The era boundary is a dated setting (`skylit_stamp_change_date: 2026-03-17`), not inferred from milliseconds, because live stamps whole seconds too.
  - Effect: pre-03-17 QQQ/SPY/NDXP boards are applied at `asOf` + 1 s + `map_latency_sec` (≈ 1.59 s) instead of `asOf` + 0.59 s, removing the ≈ 0.1–0.4 s look into the future in development months 1–6. The same function serves backtest, restart rebuilds and live (§9).
  - The live column's values are set from the stream (QQQ −0.11 s, SPX −0.06 s, NDXP −0.04 s; SPY to be decided after parity).

## R5. Floor in underlying ticks, and the proposed gate (item 5)
Floor p95, overall / at event moments:
- QQQ: 4.08 / 4.00 NQ pts = **9.8 / 9.7 QQQ cents**.
- SPY: 0.82 / 0.75 ES = **8.2 / 7.4 SPY cents**.
- SPX: the per-board R is lower than the centered floor, so the floor for SPX is the per-board value: 0.82 / 0.72 ES = **82 / 72 index ticks** (SPX 0.01).

**Proposed gate (Q8b-4), replacing F1.6 and its `mapping_p95_max` as the pass rule (F1.6 stays in the report as a diagnostic):** on the development sample (≥ 10 days per stamping era), per chain, using non-circular crossings at event moments:
- (a) **No bias:** the day-block 95% interval of the median signed error contains 0, or the median is within `mapping_bias_max` (proposed: 1 tick of the futures, 0.25 NQ / 0.25 ES). The second clause matters because with ~10,000 crossings a 0.06-pt bias, a fifth of a QQQ cent, is "significant" and harmless.
- (b) **At the floor:** the chosen estimator's p95 ≤ (1 + `mapping_floor_margin`) × the floor p95, where the floor = the lower of the centered median and the aligned per-board R, and `mapping_floor_margin` is proposed at 0.10. The recommended estimators pass: QQQ 3.94 vs 4.00, SPY 0.75 vs 0.75, SPX 0.72 vs 0.72.
- (c) **The measured p95 is carried forward** into `mapping_p95` per chain (recorded, not a limit):
  - `stop_buffer` floor = the larger of the QQQ and NDXP event-moment p95. That is 3.94 NQ from QQQ; NDXP's would be about 15 NQ under R6, which would dominate, so the owner decides whether NDXP counts.
  - The node band check: each `node_band` converted with R must be ≥ the chain's p95. Now QQQ ±0.5 $ = ±20.7 NQ ≥ 3.9; SPY ±0.5 $ = ±5.0 ES ≥ 0.75; SPX ±5 = ±5.0 ES ≥ 0.72; NDXP ±20 ≥ 15 (tight).
  - `zone_proximity` (5 NQ) stays above QQQ's 3.94.
- The spot-lag condition is replaced by an alignment check: each day's fitted offset lies within the era's measured range, or a new era is flagged.

## R6. NDXP: index-to-futures basis (item 6)
- **Contract mixing confirmed.** The 03-24/26 NDXP up-jumps equal NQU6 (median difference 0.00 against the U6 1-second closes; 84–89% within 1 pt, the rest because U6's bars are sparse). History's NDXP spot is whichever of H6/M6/U6 Skylit used.
- **NDX close source:** FRED `NASDAQ100`, free (Nasdaq data, personal use), cached at `<root>/data/raw/ndx/NASDAQ100_fred.csv`. Skylit history has none (NDXP `previousClose` is 0 on all 122 development days). Live, the stream's NDXP `previousClose` carries it (10-06: 31,224.47 vs FRED 31,224.69). Nasdaq.com's free historical download is a second free source. Nothing to buy.
- **NQ settlement:** a proxy, the VWAP of the contract's trades 15:59:30–16:00:00 ET (CME's settlement window), from Databento trades already pulled. A roll day uses the new contract's 1-second bars from the session before.
- **Method:** ρ = settle ÷ NDX close on the previous session, for the session's own contract only. Carry is decayed by time to that contract's final settlement (third Friday 09:30 ET): ln ρ(t) = ln ρ_prev × τ(t) ÷ τ_prev. An NDXP level = strike × ρ(t), all day, known before the open.
- **Development months 1–6** (124 days, scored against the same day's close basis): |error| p50 **4.7**, p95 **15.1**, max 22.2 NQ pts, signed median +0.22. Without the carry decay, p50 5.6, p95 17.0, biased +4.3. No basis (as built): p50 128, p95 224. The roll days 2025-12-17 and 2026-03-18 came out −11.5 and +2.6. The truth here (settlement VWAP vs official closing auction) is noisy itself, so these are upper bounds.
- **Out of sample, live (2026-10-07, predicted from 10-06: NQZ6 settle 31,483.24, NDX 31,224.69, basis 258.55, τ 72.7 d):**
  - At the 10-07 close, error +12.3 NQ pts (the basis fell to 242.17).
  - Against the stream's real NDX spot board by board: signed median +12.4 (10:01 recording) and +8.0 (14:59), p95 14.5 and 10.1.
  - Against 10-07's own closing basis the intraday error is only p95 3.4 and 6.4. So the error is the basis changing between days, not noise within the day.
- **Expected NDXP mapping error with this method:** about 5 NQ pts typical and about 15 at p95, all day, from the basis drift. That is about 4× QQQ's p95, and it uses up most of NDXP's ±20 band.
- **Tested and rejected:** NDX derived intraday from QQQ × κ (κ = the previous day's NDX close ÷ QQQ close). Skylit's QQQ `previousClose` in history doesn't hold up as a QQQ close (day-ahead errors up to 371 NDX pts). Live 10-07 it was −5.8 / −4.4 median, p95 7.9 / 6.2, not clearly better than the basis method. It would need an independent QQQ close source.
- A reliable basis exists, so the fallback (NDXP out of the features until Skylit answers) isn't needed. Better accuracy needs Skylit's answer: an NDX spot per board in history would make NDXP as exact as SPX.

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

## 7. Not done (needs the owner's OK). Round 2: all four pulls done 2026-10-08, each $0.0000 by `get_cost` (NQZ6/ESZ6 trades 2026-10-07, NQZ6 trades 2026-10-06, NQU6 1-second bars 2026-03-24/26), ledger in `raw/databento/ledger.jsonl`

- The live stream's convention (§2) can be measured against Databento trades for 2026-10-07 (NQ/ES `trades`, included in Standard, $0 by `get_cost`; outside the step-8 plan). Tomorrow's `parity --session 2026-10-07` also compares the stream with history board by board.
- NQU6 1-second bars for 2026-03-24/26 ($0 included) would confirm the up-jumps are U6.

## 8. Draft question to Skylit (for the owner to send)

> Subject: NDXP `spot` and the `asOf` convention in `/v1/historical/range`
>
> 1. **NDXP spot in history.** In `/v1/historical/range`, NDXP `spot` always sits on a 0.25 grid and matches CME NQ futures last trades to the tick, not the NDX index. On 2026-03-17 consecutive boards switch between the March (NQH6) and June (NQM6) contracts, about 218 points apart. On 2026-03-24 and 03-26 boards jump about +218.75 above the June contract, which matches the September contract (NQU6) to the tick. `previousClose` for NDXP is 0 in March 2026 and 30,339.33 on 2026-09-30. In the live stream, NDXP spot moves in 0.01 steps, like the index. Questions: (a) What is NDXP `spot` in history, and which contract is it on each board? (b) Were the NDXP per-strike gamma values computed against that futures price or against NDX? (c) Can history give the NDX index spot for each board (or at least each minute) over the last 365 days? (d) From which date does NDXP history carry the index spot, if it does now?
> 2. **What `asOf` means.** Up to 2026-03-16, `asOf` is a whole second, and QQQ/SPY `spot` matches the market about 0.7 s *after* `asOf` (SPX about 0.1 s before). From 2026-03-17, `asOf` has milliseconds (0.5–0.95 s past the second), and `spot` matches the market at the whole second just before `asOf` for all symbols. The live stream stamps whole seconds. Questions: (a) In history before and after 2026-03-17, and in the stream, is `asOf` the snapshot time, the publish time or the start of a 1-second bucket? (b) Which instant does `spot` describe? (c) What is the QQQ/SPY spot source (consolidated last trade, NBBO mid)?
> 3. **SPX `previousClose`** is 0 on 2026-03-06. Is that expected?
