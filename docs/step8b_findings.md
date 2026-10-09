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

## R11. The gate commands on the 22 development days (2026-10-08)
`nqbot step8b bias|estimator|alignment --dates <the 22 days of R1>`; QQQ, SPY, SPX (NDXP pending); event-moment crossings with the approved ingest rule and estimators. Reports in `<reports_dir>/step8b/`.

| Chain | (a) bias, median [95% day-block interval] | (b) chosen p95 vs best possible R | Alignment: latest fitted content (max +1000 ms) | Pass |
|---|---|---|---|---|
| QQQ (60 s) | +0.022 [−0.018, +0.065] NQ | 4.01 vs 4.05 NQ | +860 ms (2025-12-10) | all three |
| SPY (60 s) | 0.000 [−0.005, +0.005] ES | 0.77 vs 0.79 ES | +900 ms (2026-01-08) | all three |
| SPX (per board) | +0.010 [0.000, +0.018] ES | 0.73 vs 0.78 ES | −20 ms | all three |

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
- **`recorded-day`** checked on the 10-07 recording (cached trades, `--with-prev` = 10-06): content offsets QQQ −120, SPY +300, SPX −20, NDXP −60 ms; NDXP basis + carry p95 13.5 NQ (median +8.4); QQQ × k p95 7.4 / 10.2 / 8.5 / 4.0 for N = 1 / 3 / 5 / 10. 10-07 is not one of the three recordings, so it is left out of the choice (owner: N is not chosen from 10-07).
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
