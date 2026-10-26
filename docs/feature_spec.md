# Feature spec (`docs/feature_spec.md`)

> **Status: approved by the owner at step 6 (✅ gate passed 2026-10-05).** Part 1 of 3 (definitions) written 2026-10-04; part 2 (start values, F15) and part 3 (pre-declared grids, F16) added 2026-10-05. The step-6 answers (SPEC §13 "Step 6", Q1–Q39: all as recommended except Q6 = (a) and Q9 = (b)) are written into the definitions below, and the F15/F16 values into `config/`.
> Changes from here on follow CLAUDE.md rule 12 (owner approval, SPEC updated, features version bumped).

**What this file is.** It holds the exact, measurable definition of every concept in SPEC §5, plus the Skylit definitions that §5.2 defers to and the six Skylit concepts the owner approved as §5.2 model inputs on 2026-10-04 (air pockets, King dominance and migration, rainbow road, whipsaw maps, beach ball, magnets). Step 11 codes it in `src/nqbot/features/` and `src/nqbot/detectors/`; every definition here has a unit test with a hand-built example (working rule 11).

**Precedence.** SPEC.md is the source of truth for definitions; this file refines §5 and supplies what §5.2 defers. Where this file conflicts with a SPEC rule, the conflict goes to SPEC §13. Owner notes win over the Skylit docs. If owner notes and SPEC disagree, the conflict goes to §13 and SPEC is followed until the owner answers (none found; see F14).

**Values.** F0–F12 name parameters without valuing them; the proposed start values are all in one table, F15 (part 2). Exceptions valued in place: the owner's fixed definitional constants (25% of King, the 70% value area, 1σ/2σ, 30-minute TPO periods, `ib_fib_start_minute` 15 (owner 2026-10-16, was 20), `ib_lock_minute` 30, `ib_redraw_minute` 60 (owner 2026-10-16), `session.flat_by` 15:59, `min_minutes_before_flat` 14) and SPEC-fixed method constants. The golden-zone fib levels (0.5/0.618/0.705/0.79; upper fib 0.79, owner 2026-10-14) are the named owner-rule parameters `golden_fib_levels` and `golden_zone_upper_fib` in features.yaml (never tuned). Parameters marked **NEW** were not in SPEC §11 before part 1. Their start values (F15) were approved by the owner at step 6 (2026-10-05) and are in `config/`.

**Open questions** are numbered `FQ-nn` and collected in F13. Each one offered options for the owner; all were answered at step 6 (2026-10-05, F13), and the answers are written into the definitions.

## How to read an entry

| Field | Meaning |
|---|---|
| **Source** | SPEC § and/or `docs/skylit/` / `docs/owner_notes.md` file |
| **Definition** | the exact rule; a unit test can check it |
| **Inputs → known at** | every input and the earliest timestamp the bot may use it (no lookahead, §7 rule 1) |
| **Outputs** | levels, states or features it produces (features in pts, and where marked also ATR and expected-move units, F0.8) |
| **Params** | named parameters; file; kind: *owner* (fixed rule), *tuned-13* / *tuned-19* (frozen on months 1–4 at step 13 / 19, §7.1), *label* (owner-approved start, never tuned), *measured* (set from data without outcomes), *exec* (frozen execution parameter), *fold* (chosen per fold), *fixed* (start value proposed by Claude Code and approved by the owner at step 6, never tuned), *cap* (an upper bound the owner approves at step 6) |
| **Test** | the hand-built unit-test case |

---

## F0. Conventions (apply to every section)

**F0.1 Time.** Stored in UTC; session logic in America/New_York. Market data is timed by Databento `ts_event` (ns). A quantity's **known time** `known_ts` is the earliest instant the live engine could have it; a feature at time t may read only quantities with `known_ts ≤ t`.
- **Trades:** known at their `ts_event`. Records with the same `ts_event` are processed in exchange sequence order.
- **Bars** (1-second `ohlcv-1s`; 1-minute and 5-minute built from trades) are **labelled by their start** and are known at `start + length` (e.g. the 09:31 1-minute bar covers [09:31:00, 09:32:00) and is known at 09:32:00). All 1- and 5-minute bars are clock-aligned to ET minutes. A bar with no trades has no OHLC (it doesn't exist); it doesn't settle anything.
- **Skylit boards:** a board's effective time τ = floor(`asOf`) + 1 s (or floor(`asOf`) + 60 s for an uncleared 1-minute board, §3.1), one rule for every history era and live (owner 2026-10-08, step 8b: Skylit's content can sit up to the end of the stamped second); in backtest it is applied at `t_apply = τ + map_latency_sec`, live at max(arrival, τ + `map_latency_sec`). Its **content time** c = floor(`asOf`) + `board_content_offset_ms`[era][map] (F1.1) is when its `spot` was true; c ≤ τ always. Everything derived from a board (nodes, mapped levels, velocity, lean, regime, …) is known at `t_apply`. Only boards with `asOf` ≥ 09:30:00 ET of the session are applied (§3.1). Same-`t_apply` updates are applied as one batch (F5.3).
- **Profiles** (volume profile, VAH/VAL, POC, LVNs) are recomputed at clock-aligned instants every `profile_update_sec` counted from the profile's anchor, from trades with `ts_event` ≤ that instant, and are known at that instant.
- **Daily inputs:** Tempest row of session T − `tempest_lag_sessions`, dark-pool prints through the cutoff of §3.1/F4, calendar as known at `preopen_start` (08:30 ET); all known at the start of session T.
- **Outcome-dependent counts** (holds, day memory, earlier golden-zone results) count only outcomes whose resolution time ≤ t; unresolved ones are "pending".
- **Test (global, §7 rule 1):** shift every input by +1 s and by +1 bar after t and assert that every feature at t is unchanged; a bar-label test asserts that a 1-minute bar labelled 09:31 cannot be read at 09:31:59.999.

**F0.2 Sessions.**
- *CME trading day* D = [18:00 ET of the previous calendar day, 17:00 ET of D).
- *RTH* = [09:30, 16:00) ET; on an early-close day [09:30, `early_close_et`).
- *Overnight (ON)* = [18:00, 09:30) of D.
- *Week* = [Sunday 18:00, Friday 17:00).
- *Session flat-by* = min(15:59, `early_close_et` − 1 min); *entry_end* = effective flat-by − 14 min (§1.2).
- Holidays from `config/calendar.yaml` are not sessions and are skipped by every "N sessions" lookback.

**F0.3 Prices.** NQ and ES tick = 0.25 pt; NQ $20/pt, MNQ $2/pt. Features use exact floats; only order prices are rounded (F1.5). "Beyond a band" for a ceiling means strictly above its upper edge; for a floor, strictly below its lower edge. "Inside" includes the edges.

**F0.4 Front contract and rolls (§3.2).** One raw contract per instrument per CME trading day (resolved from `NQ.v.0` / `ES.v.0` at 18:00). A holiday's Globex day (not a session) keeps the previous session's contract (owner 2026-10-05); the roll lands on the next session. On the first day of a new contract every level and statistic carried from earlier days (PDH/PDL, prior RTH and week highs/lows, the weekly profile, unfilled single prints, retested extremes, the ATR and realized-vol closes, the expected-move close) is shifted by `roll_spread` = new − old contract, each taken as the last 1-second close at or before 17:00 ET of the previous trading day. Known at the session start (pre-open). **Test:** a carried PDH of 20,000 with spread +250 becomes 20,250 on the roll day; nothing is shifted on other days.

**F0.5 Settled bar (§5.5, owner).** A closed bar of length L *settles beyond* a band in a direction when its **open** is beyond the band as of the bar's open time and its **close** is beyond the band as of its close time (bands can move inside the bar), each by at least the applicable margin (`flip_margin` for flips, 0 otherwise). Known at bar end. Used for the 1-minute chart-level cross (F5.2), the golden-zone invalidation (F10), the breakeven rule (F11.3) and every flip (L = `flip_bar_minutes`).
**Test:** a ceiling band [100, 102]; bar open 102.25, close 103 → settled above; open 101.75, close 103 → not settled; band moving to [100, 104] at the close → not settled.

**F0.6 Roles and direction.** A level's *role* is **ceiling** (trade direction short) or **floor** (long). Approach from below → ceiling; from above → floor (§5.4 Direction). After a flip the role reverses.

**F0.7 Nulls.** A feature whose input isn't known yet (no board applied, no profile, no gameplan, fewer sessions than a lookback) is null, never zero or filled forward from another session. LightGBM handles nulls; the logistic and day-type models use the missing-indicator encoding chosen at step 15.

**F0.8 Vol scaling (§5.4 Distances).** Every distance feature is stored in three forms: **points**, **ATR units** and **expected-move (EM) units**.
- *ATR* = the average true range of the last `atr_period` **completed RTH sessions** (RTH high, low, previous RTH close; roll-shifted), known at the session start. The average is the simple mean of the true ranges (owner 2026-10-05, FQ-01).
- *Expected move* (NQ) = C × m, with C and m exactly as in F3.11: C = the NQ RTH close of session T − 1 (front contract, roll-shifted to today's), m = `em_1d_pct` ÷ 100 of QQQ's pre-open row of session T − `tempest_lag_sessions`, **as stored, never recomputed** (Tempest's own one-day move in calendar time; owner 2026-10-22, SPEC §13 Q-svxwknd). ES uses the ES close × SPY's `em_1d_pct`. The EM unit and the expected-move lines are the same number, so a distance of 1 EM is the distance to a ±1σ line.
- **Test:** a hand-built 3-session ATR; C = 20,000 with `em_1d_pct` 0.80 → EM 160.0 pts; a Friday row with `em_1d_pct` 1.400 (weekend minutes in it) → Monday's EM 280.0 pts, the stored value used as it is.

**F0.9 Instruments of a level.** Nasdaq maps (QQQ, NDXP), NQ chart levels, QQQ dark pools and gameplan zones live on **NQ** and are checked against NQ trades. S&P maps (SPY, SPX), SPY dark pools and ES chart levels live on **ES**, are checked against ES trades and are cross-market features only (they never create NQ events).

**F0.10 Strike counts.** Every parameter or feature counted "in strikes" (pika-cloud and air-pocket adjacency, `last_king_shift_strikes`, the roll shifts in F2.11) counts **positions in the board's sorted strike list** (the strikes present on that board), never price ÷ a nominal spacing, because strike spacing differs between QQQ, NDXP, SPY and SPX. The one exception is the rug distance (F2.13), which is a price distance measured in **strike steps**: a map's strike step on a board = the most common gap between consecutive listed strikes, both within ±`regime_band`% of spot, per board (where listed strikes mix 5- and 10-point spacing near spot, as on NDXP/SPX, it can be 5 on one board and 10 on the next; owner 2026-10-25, Q27, unit-tested); a tie between gaps goes to the smaller gap (owner 2026-10-26, SPEC §13 Q-strikestep-tie). Signed counts (`roll_steps`, `last_king_shift_strikes`) count the listed strikes between the two strikes, the far one included.

**F0.11 ES equivalents of NQ-point parameters (owner 2026-10-05, FQ-21).** ES is used only for cross-market reads (Trinity, SMT divergence, relative delta, ES levels as features), never traded. Every NQ-point parameter applied on ES (`zone_proximity` → `es_zone_proximity`, `rearm_dist`, `sweep_max_depth`, `flip_margin`, `confluence_tol`, `max_zone_width`, chart-level half-widths) uses the NQ value × ES ÷ NQ price at the session open (the first RTH trade of each), fixed for the session. *Test:* NQ 30,600, ES 7,000 at the open → `rearm_dist` 10 NQ pts = 2.29 ES pts.

---

## F1. Mapping strikes to NQ/ES (§5.1)

**F1.1 Ratio R.**
- *Definition:* for each applied board of map m, R_m = F ÷ S. S = the board's own `spot`; F = the price of the last trade of the mapped futures (NQ for QQQ/NDXP, ES for SPY/SPX, session front contract) with `ts_event` ≤ c, the board's content time: c = floor(`asOf`) + `board_content_offset_ms`[era][map], era = before / from `skylit_stamp_change_date` (2026-03-17; live is the later era, parity 2026-10-07), the measured median offsets (step-8b findings R4); live uses the `live` row (the post-03-17 values; SPY's live offset and NDXP pending the 2026-10 recordings). The board's levels are converted once with the map's R in force (F1.7) (`level = strike × R`, band edges = (strike ± half-band) × R) and stay fixed until the next accepted update of that map. R is never carried across a roll or across sessions.
- *Inputs → known at:* `spot`, c, τ (board, at `t_apply`); F (trade, at its `ts_event` ≤ c ≤ τ < `t_apply`). The step-8b alignment check fits c per recording against the futures and alerts if a content time falls after floor(`asOf`) + 1 s.
- *Outputs:* R per map; mapped node levels and bands.
- *Test:* QQQ strike 750, spot 749.12, F = 30,600.00 → R = 40.84793…, level = 30,635.95…; also a test that F is the trade at or before c and never a later one (e.g. a trade 1 ms after c is not used); a pre-03-17 QQQ board stamped 10:00:00 reads F at 10:00:00.740 and is applied at 10:00:01 + `map_latency_sec`.

**F1.2 Outlier rejection and re-accept.**
- *Definition:* applies to maps whose R is per board (SPX; `ratio_window_sec` 0); a trailing median (F1.7) takes the board ratios unfiltered. A new R is **rejected** if |R − median(last 5 accepted R of that map, this session)| ÷ that median > `ratio_outlier_bps` × 10⁻⁴. A rejected update still applies its new strike values, converted with the last accepted R, and is logged. If `ratio_reaccept_n` consecutive updates are rejected and all lie within `ratio_outlier_bps` of each other (pairwise relative to their own median), the latest is accepted, the median history is reset to it and the day is flagged `ratio_shift_day`. With fewer than 5 accepted values the median uses those available; the first board of the session is always accepted.
- *Params:* `ratio_outlier_bps`, `ratio_reaccept_n` (features.yaml `mapping:`; SPEC start values; step 8b reports the rejection rate, and frequent rejections mean finding the cause, not raising the threshold).
- *Test:* accepted R values 40.80 ×5, then 40.95 → rejected (36.8 bps); two more values at 40.95 → the second of them is accepted (3 consecutive rejections, the first rejected update included) and the history reset.

**F1.3 Opening skip.**
- *Definition:* SPX and NDXP boards with `asOf` < 09:30:00 + `ratio_open_skip_sec` are not applied; their maps are null until the first later board. QQQ and SPY are unaffected.
- *Params:* `ratio_open_skip_sec` (SPEC).
- *Test:* an NDXP board with `asOf` 09:30:30 is not applied and the NDXP map stays null; a QQQ board at 09:30:30 is applied.

**F1.4 Node band.** Half-band per map `node_band` {QQQ, SPY, SPX, NDXP} in the underlying's units (owner; NDXP confirmed at step 13). The band of strike k is [k − b, k + b] in underlying units → [(k − b)·R, (k + b)·R] on the futures.

**F1.5 Order-price rounding (§5.1).** Order prices only, to 0.25: stops **away** from entry, targets **toward** entry, passive limit entries to the **passive** side (a long limit down, a short limit up). Marketable and stop-limit limits are `max_entry_slip_ticks` beyond their reference (F7). **Test:** a long stop at 30,100.10 → 30,100.00; a long target at 30,150.10 → 30,150.00; a short target at 30,050.10 → 30,050.25.

**F1.6 Precision metric (gate 8b, not a model feature).** At each applied board of map m with a known futures price at τ: error = |strike × R_prev − strike × R_cur| for each relevant node of that board (R_prev = the R in force just before). Reported per chain as median / p95 / p99 / max, by time of day and by realized-vol tercile. A **diagnostic** next to `mapping_p95_max`, not a gate condition (owner 2026-10-08, SPEC §13 Q8b-4); the pass rule is §5.1 (bias, estimator within 10% of the best possible R, the alignment check, the owner's Atlas replay check; the share of Atlas crossings within the frozen `atlas_match_tol`, pooled over all sample days, is a regression check, alert below `atlas_regression_min_share`, SPEC §13 Q8b-5).

**F1.7 R estimator per map (owner 2026-10-08, step 8b; SPEC §5.1).**
- *Definition:* the R in force for map m after an applied board = the median of the board ratios (F1.1) of map m whose content time lies in (c − w, c], w = `ratio_window_sec`[m], over the boards since the last reset; w = 0 means the board's own ratio with F1.2. Values: QQQ 60, SPY 60, SPX 0, NDXP pending (§13 Q8b-1: basis + carry or QQQ × k with N ∈ {5, 10, 20}, chosen leave-one-day-out by the best held-out p95 against the real NDX on the owner's three session recordings; `features/ndxp.py`). Trailing only, never centred. **Reset** at the session's first applied board (after `ratio_open_skip_sec` for SPX/NDXP, F1.3), at a contract change (only between sessions, F0) and after a gap between consecutive applied boards of the map longer than `ratio_gap_reset_sec` (10 s). **Warm-up:** after a reset the median uses the boards since it, the first board alone at first, no minimum count. One function for backtest, restart rebuilds and live.
- *Params:* `ratio_window_sec`, `ratio_gap_reset_sec` (features.yaml `mapping:`; fixed, from the step-8b study: event-moment level error p95 QQQ 0.55 NQ, SPY 0.11 ES, findings R8).
- *Inputs → known at:* each board's ratio at its `t_apply`; the median at the current board's `t_apply`.
- *Test:* QQQ boards at content times 0, 30, 61, 62, 74 s with ratios 41.0, 41.2, 41.1, 50.0, 41.3. With `ratio_gap_reset_sec` 10 every gap but 61→62 resets: R in force 41.0, 41.2, 41.1, 45.55 (median of 41.1, 50.0), 41.3. With the reset at 60 s (illustration only): 41.0, 41.1, 41.15 (window (1, 61]: 41.2, 41.1), 41.2 (41.2, 41.1, 50.0), 41.25 (window (14, 74]).

**F1.8 Mapping-dislocation guard (owner 2026-10-14; SPEC §5.1, §9, §13 Q-dislguard; `features/disloc_guard.py`).**
- *Definition:* per chain m in `disl_guard_maps` (QQQ, SPY, SPX) and session, boards in apply-time order (`t_apply`, F0.1). **Per-board trigger:** r_k = board k's own ratio (F1.1, F at its content time ÷ its spot); held_high_k = min(r_k, max r of the boards in [t_k − `disl_guard_confirm_sec`, t_k)), held_low_k = max(r_k, min of those) (undefined with no such board); board k is flagged when, over the boards in (t_k − `disl_guard_window_sec`, t_k], max held_high − min held_low > `disl_guard_pct` × median r. **Bot's-R trigger (backstop):** with q_k = the R in force after board k (F1.7), flagged when (max q − min q) ÷ median q over (t_k − window, t_k] > `disl_guard_pct`. Both windows reset only at the session's first board (owner 2026-10-17, SPEC §13 Q-dislguard-2 (b)(i)): a board gap does not reset them (`ratio_gap_reset_sec` resets F1.7's R only), so the 2-minute window runs by time across a gap and a move made inside it fires on the second board after it (the bot's R on the first). The chain is **blocked** at time t when some flagged board (either trigger) lies in [t − `disl_guard_hold_sec`, t] (trailing only). A **fire** = a trigger's first flagged board outside that trigger's current block; logged with trigger, chain, size (the range at that board) and peak. No opening-minutes exception. A data-quality guard (rule 6): only mapping inputs, thresholds never tuned on trade results.
- *Effect:* a pair is not tradeable (F5.7 `tradeable`) when its `decision_ts` is inside a block of a chain with a member in its zone (NDXP follows QQQ's block if its R comes from QQQ × k); live and both simulators remove pending entries at those zones at the block start and send no new entry there; open trades keep their brackets.
- **During a block (owner 2026-10-17, SPEC §13 Q-dislguard-2 (a); `features/block_state.py`):** the blocked chain is treated like a feed gap on that chain. Its R-dependent features are **null** (F0.7: unknown, never the last value carried) for every decision inside the block: F2.6 node state, F2.7 beach ball, F2.10 magnet pull, F2.11 ceiling / floor pause and resume, F2.15 air pockets, F2.19 Trinity (`trinity_aligned`, `trinity_count`, `maps_with_node_here` and its node stats), F2.20 path nodes, F5.16 `es_at_level` / `es_stack_count` and that chain's maps in the F10 golden-zone node features (QQQ's too, for events not on QQQ zones; QQQ zones are blocked already). Not affected (no R): King, % of King, node type and sign, velocity and lean, regime, rug / pika, rainbow, King dominance / migration, whipsaw, SMT, relative delta and the ES chart levels. Its F2.6 and F2.11 state machines **pause** (no transition from trades judged against a band mapped with the blocked R), and target selection (F11.2) skips its path nodes while it is blocked. **At the block end** each paused state is judged at once against the band then in force, as a level check, not a transition: a node that the last trade at or before the block end is beyond (ceiling: above its upper edge; floor: below its lower edge) is marked crossed at the block end time, so a real cross made during the block is not missed waiting for a new trade through the band; a cross undone before the block end leaves nothing (the band it crossed was not known). History, labels and both simulators call the same `resume` at each block end; live calls it when the clock passes the block end (rule 4).
- *Live board drops:* after a per-map freshness block (`skylit_map_stale_sec`, live only) the map stays blocked until a second board arrives within `disl_guard_confirm_sec` of the first (owner 2026-10-17, SPEC §13 Q-dislguard-2 (b)(ii)): the R right after a gap rests on one board this guard cannot yet check; a first board not confirmed in time is replaced by the next board as the new first; every case is logged (`live/map_freshness.py`).
- *Params:* `disl_guard_pct`, `disl_guard_window_sec`, `disl_guard_confirm_sec`, `disl_guard_hold_sec`, `disl_guard_maps` (features.yaml `mapping:`; owner, fixed from data quality: 0.002, 120, 10, 60, [QQQ, SPY, SPX]).
- *Inputs → known at:* each board's ratio and the R in force at its `t_apply`.
- *Test (hand-built, 1-s boards at R = 41.0):* one board 50 bps up (or down), the next back → no fire; a 25-bp shift held by every later board from board 100 → fires at board 101 (both levels held by two boards); one board 19.8 bps down at 100, then +1 bp from 101 → no fire (its starting level is held by one board only); the same with board 99 also down → fires at board 102; R in force stepping 0.25% at board 120 → a bot's-R fire at 120; after the shift at 100 the block lasts until 60 s after the last flagged board (218 → 278); a 25-bp shift made inside a 21-s gap (boards to 49 s, then from 70 s) → the per-board trigger fires at 71 s, the bot's R at 70 s; two sessions never share a window. During a block: a floor at 100–101 that price falls through inside the block and is still below at the block end → crossed at the block end; a band mapped wrongly to 103–104 inside the block with price at 102 → nothing marked, a real cross after the block counts at its trade; a cross undone inside the block → nothing; R-dependent features null inside the block only. Live: the first board after a gap leaves the map blocked; a second board 12 s later is the new first; one 8 s after that confirms it.

---

## F2. Skylit structure (§5.2, `docs/skylit/`)

### F2.1 Board ingest and map state
- *Source:* §3.1 Ingest, §5.2.
- *Definition:* a **map** = the latest applied board of one symbol (QQQ, NDXP, SPY, SPX) within the `skylit_max_strikes` window. A strike absent from a board (outside that board's `skylit_max_strikes` window) is **unknown** on that board, never 0: it has no value, no mapped level and null velocity; its relevance and F2.6 state are frozen until it reappears; King and % of King use only the strikes present; comparisons across boards (velocity, rolling shrink, King migration) use only strikes present on both boards. **Two clocks (owner 2026-10-25, Q26):** everything computed per board uses that board's own `spot` at its `t_apply`: node types and the confirmed King (F2.5, F2.16), ceiling and floor candidates (F2.11), rug (F2.13), the lean's sides (F2.9) and regime (F2.12). Futures trades (NQ for Nasdaq maps, ES for S&P maps) against the mapped bands decide the ceiling/floor pause and resume and the F2.11 band checks (the confirmed-ceiling exception and the re-choice by price), crosses (F2.6), Trinity (F2.19) and magnets (F2.10). `map_resolution_1m` = the board's 15-minute call window was served at 1-minute resolution (`meta.resolution`).
- *Known at:* `t_apply` of each board.
- *Outputs:* per map: the strike → value table, `spot`, `asOf`, τ, R; `map_age_sec` = t − τ of the latest applied board (a feature); `map_resolution_1m` (session/window flag).
- *Test:* a board missing strike 760 that the previous board had → 760 has null velocity and keeps its relevance state; King is taken over the present strikes only; a board with `asOf` 09:29:59 is not applied; QQQ spot 759.2 while NQ is still inside 759's band → per-board features (types, rug, lean) treat 759 as below spot, but 759 stays the confirmed ceiling (F2.11, `tests/test_rolls.py`).

### F2.2 Node value and sign (positive / negative nodes)
- *Source:* `gamma-regime-positive.md`, `gamma-regime-negative.md`, owner notes ("positive vs negative nodes as in the Skylit docs").
- *Definition:* a node's **value** is the per-strike gamma `value` of the board. **Positive node** (yellow, "pika" colour, dealers long gamma: they buy dips and sell rips, which slows price) = value > 0. **Negative node** (purple, "barney" colour, dealers short gamma: they hedge with the move, which speeds price) = value < 0. **Magnitude** |value| is the measure of influence ("magnitude over colour", Absolute Value Rule); sign is a separate feature.
- *Check (Claude Code, step 7/11, no owner input unless it fails):* on the 40 `/v1/historical` instants, Skylit's `nodeType` pika must occur only on value > 0 and barney only on value < 0. If not, stop and ask the owner (the sign convention would be reversed or different).
- *Outputs:* `node_sign` ∈ {+1, −1}, `node_abs_value`.

### F2.3 King and % of King
- *Definition:* **King** of map m at a board = the strike with the largest |value| on that board (within the window). Ties go to the strike nearer `spot`, then the lower strike (a deterministic rule that only matters for identical floats). `pct_of_king(k)` = |value_k| ÷ |value_King| ∈ [0, 1]. Sizes are compared only within a map (§5.2).
- **Board King vs confirmed King (owner 2026-10-25, Q28):** the board's own King (this definition) is the denominator of `pct_of_king` (so relevance F2.4 and the 25%/20% rule, air pockets F2.15, pika clouds F2.14, magnets F2.10) and of `king_dominance` (F2.16), so % of King never exceeds 1. "Who is the King" uses the **confirmed King** (F2.16): `is_king`, `king_present`, `king_distance`, the Gatekeeper rule (F2.5) and the migration features.
- *Outputs:* `is_king` (the confirmed King), `pct_of_king`, `king_present` (zone feature: the confirmed King is among the zone's node members).
- *Test:* values {740: +2.0e6, 745: −3.0e6, 750: +1.5e6} → King 745 (negative King allowed), pct(740) = 0.667.

### F2.4 Relevant nodes, hysteresis and identity
- *Source:* §5.2 Relevant nodes and Node identity; owner notes (25%).
- *Definition:* node identity = (map, strike). A node **becomes relevant** at the first applied board where `pct_of_king` ≥ `min_node_pct_of_king` (0.25, owner) and **stops being relevant** at the first applied board where `pct_of_king` < `node_drop_pct`. In between it keeps its state. **While a node is not relevant** its F2.6 state (role, crossed / provisional / flipped, `flip_count`) and its freshness are frozen (trades change nothing) and resume if it becomes relevant again the same session (owner 2026-10-25, Q5; as for a strike missing from a board, F2.1); its identity (map, strike) is unchanged. `pct_of_king` is always against the King of that same board (F2.3, over the strikes the board carries); a strike missing from a board keeps its state (F2.1). The state starts fresh (not relevant) at each session's first applied board (owner 2026-10-11: history is RTH-only and the 0DTE chain is a new expiration every day). One function, `features/nodes.py` `relevance`, for the step-8b checks and step 11 (owner 2026-10-11). Only relevant QQQ/NDXP nodes are NQ registry levels; relevant SPY/SPX nodes are ES cross-market levels.
- *Params:* `min_node_pct_of_king` (*owner*), `node_drop_pct`.
- *Outputs:* `relevant` flag, `minutes_since_relevant`.
- *Test:* pct 0.24 → 0.26 (becomes relevant) → 0.22 (stays) → 0.19 (drops); a fresh strike at 0.22 is not relevant; a non-applied board neither counts nor changes the state (`tests/test_nodes.py`).

### F2.5 Node types (King, Gatekeeper, significant, normal; pika/barney colour)
- *Source:* §5.2 Node types; `pattern-gatekeeper.md`; colours from the regime docs; **owner answer 2026-10-05** (FQ-02/FQ-03: "gatekeepers and significant nodes are at least 25% the magnitude of the King").
- *Definition:* every node gets one **size/position type** and, separately, its **colour**:
  - **King** = the confirmed King (F2.16; owner 2026-10-25, Q28), not the board's largest |value| (F2.3), so a 760/759 tug-of-war doesn't flip the types.
  - **Gatekeeper** = a relevant node (F2.4, ≥ 25% of King), other than the confirmed King, whose strike lies strictly between the board's spot and the confirmed King's strike (owner 2026-10-05; the confirmed King 2026-10-25, Q4/Q28: the rule is kept against Skylit's API examples, which Q3's mapping explains). Every such node is a Gatekeeper; no other node is required beyond it. Relevant nodes on the far side of price from the King are not Gatekeepers.
  - **Significant** = any other relevant node.
  - **Normal** = a node that is not relevant (pct_of_king below the F2.4 cutoff, with its hysteresis).
  - **Colour:** pika = positive (yellow), barney = negative (purple) (F2.2); a feature of its own, combined with any type (e.g. a pika King).
- *Known at:* `t_apply` of each board (spot and the relevant set from the same board, the confirmed King as of that board).
- *Outputs:* `node_type` (one-hot: king / gatekeeper / significant / normal), `node_sign`; zone features `king_present`, `gatekeeper_present`; `gatekeepers_to_king` = the number of Gatekeepers between price and the King; `gatekeeper_vs_beyond` = a Gatekeeper's |value| ÷ the largest |value| between it and the King, the King included (the doc's "far larger than the nodes beyond it"); the S&P path features of F2.20.
- *Validation (owner 2026-10-25, Q3):* step 11 compares only King and Gatekeeper with Skylit's `nodeType` on the 40 step-7 instants: Skylit king ↔ bot King; Skylit gatekeeper (any non-King strike ≥ 25% of King, Skylit's size-only rule, api-reference/node-classification) ↔ bot Gatekeeper ∪ significant (relevant non-King). Skylit's pika/barney labels are not compared as types (for the owner they only mean positive/negative, the bot's node sign F2.2); the only check on them is that Skylit's pika strikes are positive and barney strikes negative (F2.2). Reported as a confusion table; the bot is classified over the same strike window those instants were pulled with, and the report states the `maxStrikes` used. Step 12 shows the owner; disagreements go to the owner, who decides whether the definition changes.
- *Test:* spot 748; relevant nodes 745 (+), 750 (+), 755 (−), King 760 → 750 and 755 Gatekeepers (between price and the King), 745 significant (on the other side of price), 760 King; a node at 752 at 0.15 of King → normal; with spot 757 → nothing lies between price and the King, so there are no Gatekeepers and 750/755 are significant. Confirmed King: 760 and 759 alternating as the board's largest every 30 s → the confirmed King stays 760 and the Gatekeeper set is unchanged; a clean new King 755 → the Gatekeeper set changes at the board that confirms it, not earlier (`tests/test_node_switch.py`).

### F2.6 Node state machine (crossed / flipped / reclaimed; owner)
- *Source:* §5.2 Crossed vs flipped; owner notes; `pattern-*-roll*.md` ("gamma flip: price breaks and holds above a key node, which may then become a floor").
- *Definition:* per relevant node, on its own instrument (F0.9), with band B(t) from the latest accepted R:
  1. **Armed / role.** When a node becomes relevant (or after any state reset) it has no role until price is ≥ `rearm_dist` outside B. The role is *ceiling* if price is below B, *floor* if above.
  2. **Crossed** (setup invalid): the first trade **beyond** B relative to the role (ceiling: price > upper edge; floor: price < lower edge). Known at that trade. B is the node's full band (F1.4: strike ± `node_band`, QQQ/SPY ±0.5, SPX ±5, NDXP ±20, mapped with R), so a trade at or through the strike itself inside B is not a cross; `zone_proximity` only affects touches (F5.6) and the Trinity overlap, never crosses (checked 2026-10-26, owner).
  3. **Flipped:** while crossed, a `flip_bar_minutes` bar settles beyond B on the crossed side by ≥ `flip_margin` (F0.5). (The bar is clock-aligned and its open must already be beyond B, so a cross at 10:12 flips at 10:20 at the earliest; intended, owner 2026-10-25, Q22.) The provisional new role (step 5) is confirmed: `flip_count` += 1 and `provisional_role` false; the node is armed in that role once price is `rearm_dist` away on the new side. After a flip, a cross back is a new cross (step 2) of the new role. Flips repeat both ways.
  4. **Reclaimed:** while crossed (not yet flipped), price trades back through B to the original side and reaches ≥ `rearm_dist` beyond the original-side edge. The node returns to its original role, `reclaimed` = true and the event re-arms (§5.2).
  5. **Provisional new role (post-cross retests; owner 2026-10-23, SPEC §5.2).** From the cross until a flip or a reclaim the node holds the opposite role provisionally (`role` reversed, `provisional_role` true). It arms in that role once price is ≥ `rearm_dist` beyond B on the crossed side (the F5.6 rule), and a touch from the crossed side is an event in that role, a **post-cross retest**, carrying the F2.6b cross features. A cross back toward the original side by the node's own rule (here: the first trade beyond B on the original side) ends the provisional role, which is that event's break (F11.4); the same rule applies whether or not a retest event happened (owner 2026-10-25, Q24); the node then creates no events until it is reclaimed (step 4) or price trades beyond B on the crossed side again, which resumes the same cross episode (its features keep counting from the first cross). While a break is unresolved (F3.9 sweep window, F5.2 no settled 1-minute bar) there is no event. If the step-15 test fails (F5.15), this step reverts to: while crossed, the node creates no events, and re-entering B from the crossed side is not a touch.
  6. **During a mapping-dislocation block of the node's chain** (F1.8; owner 2026-10-17): no transition; at the block end the state is judged at once against the band then in force (a node price is beyond is crossed at the block end time). The node's outputs are null inside the block.
- *Known at:* each trade (cross, reclaim) and each flip-bar close.
- *Outputs (per node and copied to zone members):* `role`, `crossed`, `provisional_role`, `flip_count`, `minutes_since_flip`, `minutes_since_cross`, `reclaimed`, freshness per role (F5.11), and the F2.6b cross features.
- *Params:* `rearm_dist` (SPEC start; one global value), `flip_margin` (per level type; *tuned-13*), `flip_bar_minutes` (owner, 5).
- *Node arming vs zone arming:* the node-level arming in step 1 (≥ `rearm_dist` outside the node's own band) only sets the node's role and state. Touches and event direction always come from the zone rule F5.6 (`zone_proximity` + `rearm_dist`; owner 2026-10-25, Q23: F5.6 governs touches, so a retest arms once price is outside the zone's re-arm band).
- *Test:* a ceiling band [100, 101]; trade 101.25 → crossed; 5-minute bar open 101.5, close 102 → flipped to floor, `flip_count` 1; separately, cross then a trade at 100 − `rearm_dist` (≥ `rearm_dist` below the original-side edge 100) → reclaimed; a trade at 100.25 − `rearm_dist` → not yet. Post-cross retest: the same ceiling; trade 101.25 → crossed, provisional floor; a trade at 101 + `rearm_dist` → armed as a floor; a trade back inside the touch band → an event, direction long, `flip_count` 0, `provisional_role` true; then a trade at 99.75 → that event's zone is crossed (broke) and no event follows until the reclaim at 100 − `rearm_dist`; with a trade at 101.25 instead, the cross episode resumes.

### F2.6b Cross features (owner 2026-10-23; every level type)
- *Source:* owner 2026-10-23 (SPEC §5.2 Post-cross retests, §13 Q-flipretest).
- *Definition:* per level and per **cross episode** (from the cross by the level's own rule (F2.6 step 2, F5.2, F3.9's break, a gameplan zone's far edge, F5.9) until a reclaim, or until the next cross after a flip), from trades ≤ t on the level's own instrument (F0.9), with band B(t):
  - **Distance:** `xb_depth` = the deepest price beyond B's crossed-side edge since the cross, in points, ATR and EM (F0.8).
  - **Volume:** `xb_vol_rel` = volume traded beyond B since the cross ÷ the time-of-day normal over the same seconds (F9.10's N(m), prior 20 full sessions; partial minutes pro rata); `xb_delta_rel` = buy − sell volume traded beyond B, same scale, positive in the cross direction.
  - **Time:** `xb_sec_beyond` = seconds price spent beyond B since the cross (from each trade beyond to the next trade); `xb_settled_1m` = settled 1-minute bars (F0.5, the §5.5 settled bar) beyond B since the cross (owner 2026-10-24: levels can flip quickly); `xb_settled_5m` = settled `flip_bar_minutes` bars (F0.5) beyond B by ≥ `flip_margin` since the cross (≥ 1 is the F2.6 step-3 flip, the old rule as a feature).
  - **Reclaim:** `xb_reentries` = times price came back inside B during the episode without a reclaim; `cross_count_today`.
  - **Age:** `minutes_since_cross` (F2.6).
  - **Skylit nodes only:** `xb_node_pctking_chg` = pct_of_king at t − at the cross; `xb_node_sign_flipped` = the node's sign at t differs from its sign at the cross (both from boards applied ≤ t). Null for every other type.
  - **Gameplan zones:** B = the zone; `flip_margin` 0 (containers have no flip-margin key).
- *Outputs:* per member, on events of a level in a post-cross role (provisional or flipped) from that episode; null for a level in its original role with no open episode and during a mapping-dislocation block of the node's chain (F2.6 step 6). Zone features: the primary member's values and `members_post_cross` (the number of members in a post-cross role).
- *Known at:* each trade; `xb_settled_5m` at each flip-bar close; the node change at each board's `t_apply`.
- *Params:* none new (`rearm_dist`, `flip_margin`, `flip_bar_minutes`, F9.10's normals).
- *Test:* a ceiling [100, 101]; trades 101.5 and 103 → `xb_depth` 2.0 pts; back to 100.5 → `xb_reentries` 1; 300 contracts traded beyond against a normal of 150 over the same seconds → `xb_vol_rel` 2.0; a 5-minute bar opening 102 and closing 102.5 → `xb_settled_5m` 1; 1-minute bars (open, close) (101.5, 102), (102, 100.75), (101.25, 101.5) after the cross → `xb_settled_1m` 2 (the middle bar closes inside B). Causal test: every value at t is unchanged when the data after t is removed.

### F2.7 Beach ball (overshoot), owner-approved 2026-10-04
- *Source:* `pattern-beach-ball-overshoot.md` ("price overshoots a node, moving beyond it, then reacts after punching through… overshoot → reaction → potential reversion; not a breakout").
- *Definition:* a **beach ball** is a crossed → reclaimed episode of a GEX node (F2.6 step 4). Each reclaim records:
  - `bb_overshoot_depth`: the deepest price beyond the crossed-side edge during the crossed episode (pts, ATR, EM);
  - `bb_seconds_beyond`: from the cross to the first trade back inside B;
  - `bb_seconds_to_reclaim`: from the cross to the reclaim.
  A cross that ends in a flip is not a beach ball.
- *Outputs (node and zone features, model input only):* `beach_ball_count_today`, and the last episode's depth, seconds beyond and minutes since its reclaim; null if none.
- *Params:* none new (`rearm_dist`).
- *Test:* a ceiling [100, 101]; trades 101.5, 103, 100.5, then 100 − `rearm_dist` → one beach ball, depth 2.0.

### F2.8 Velocity
- *Source:* §5.2 Velocity; regime docs ("velocity mode shows the % change of nodes").
- *Definition:* for node k on the board with effective time τ: `velocity_k` = (v_k(τ) − v_k(τ′)) ÷ |v_k(τ′)| (signed; it matches Skylit's `velocityPct`, owner 2026-10-25, Q1), where τ′ is the effective time of the latest applied board of that map with τ′ ≤ τ − `velocity_lookback_sec` (same session). Null if no such board, or if v_k(τ′) = 0. The lean (F2.9) uses the **magnitude velocity** `mvel_k` = (|v_k(τ)| − |v_k(τ′)|) ÷ |v_k(τ′)| (same board pair and nulls), so a negative node growing from −1M to −2M has velocity −1.0 but magnitude velocity +1.0 (building).
- *Known at:* `t_apply` of the current board.
- *Params:* `velocity_lookback_sec`.
- *Test:* boards at τ = 0, 30, 60, 90 s, lookback 60 → velocity at 90 uses the board at 30; at 60 it uses 0. A barney node −1M → −2M → velocity −1.0, magnitude velocity +1.0 (`features/lean.py`, `tests/test_lean_rug_trinity.py`).

### F2.9 Velocity lean
- *Definition (sign and input fixed by the owner 2026-10-25, Q1/Q2):* per map at each applied board: `lean_raw` = Σ_{relevant k, strike > spot} mvel_k × pct_of_king_k − Σ_{relevant k, strike < spot} mvel_k × pct_of_king_k (mvel = the F2.8 magnitude velocity; nodes with null velocity and a strike exactly at spot are skipped; spot = the board's own, F2.1). `lean` at t = the time-weighted average of the step function `lean_raw` over [t − `lean_window`, t]; only time after the first applied board counts (time-weighted, owner 2026-10-05, FQ-20). **Positive = accumulation above spot = bias up** (F2.18, SPEC §5.2): one sign everywhere.
- *Params:* `lean_window` (minutes; owner 2026-10-05: 1, "accumulation shifts quickly"). With a 1-minute window, a 1-minute-resolution history day has about one board per window, so time-weighting (FQ-20) matters little there. The lean is noisier than with the 5-minute proposal, so step 12 checks `bias_deadband` (F2.18) against the 1-minute lean's distribution.
- *Outputs:* `lean_<map>` for each of the four maps.
- *Known at:* `t_apply` of each board (`lean_raw`); t (`lean`, using boards applied ≤ t).
- *Test:* `lean_raw` = 1.0 from t−10 min to t−4 min and 0.4 from t−4 min to t, `lean_window` 10 → `lean` = (6×1.0 + 4×0.4)/10 = 0.76. Sign: a relevant node above spot building (mvel +0.5 at 1.0 of King) → `lean_raw` +0.5, bias up; the same below spot → −0.5, bias down (`features/lean.py`, `tests/test_lean_rug_trinity.py`).

### F2.10 Nodes as magnets (size ÷ distance), owner-approved 2026-10-04
- *Source:* regime docs, Absolute Value Rule ("nodes act like magnets. The bigger the node, the stronger the pull. The closer the node, the stronger the pull").
- *Definition:* per map at t, for relevant nodes: `pull_k` = pct_of_king_k ÷ max(d_k, `magnet_min_dist_em`), where d_k = |mapped level_k − price| in EM units. `magnet_pull_above` = Σ pull over nodes above price; `magnet_pull_below` = Σ below; `magnet_net` = above − below. Event features: also the pull in the trade direction vs against it.
- *Known at:* t (price = last trade of the map's instrument; levels at the last `t_apply`).
- *Params:* `magnet_min_dist_em` (**NEW**; the floor keeps a node at price from dominating).
- *Test:* two nodes 0.5 EM above (pct 1.0) and 1.0 EM below (pct 0.5) → above 2.0, below 0.5, net +1.5.

### F2.11 Rolling ceilings and floors
- *Source:* `pattern-ceiling-rolldown.md`, `pattern-floor-rollup.md`, `gamma-regime-negative.md`; **owner answer 2026-10-05** (FQ-05: "you are usually looking at the highest-magnitude node above price; if a node below it grows and becomes the new highest-magnitude node above price, that is a ceiling roll-down", e.g. QQQ 760 dies down and 759, still above price, becomes the largest).
- *Definition:* per map, at each applied board (switching and price moves revised by the owner 2026-10-25, Q6–Q9; `features/rolls.py`, `features/node_switch.py`):
  - **Candidates:** ceiling candidates = strikes above the board's spot (F2.1 two clocks); floor candidates = strikes below it. Exception: the confirmed ceiling stays a candidate, and the ceiling, while the futures price (NQ for Nasdaq maps, ES for S&P) has not traded beyond its band's upper edge, even if the board spot is above its strike (floor: below its lower edge), so the ceiling choice and the pause can't disagree. A node the futures crossed is excluded until a trade back below its lower edge (floor: above its upper edge). Each board's ceiling **leader** = the largest |value| among the ceiling candidates (owner: magnitude, either sign; ties nearer spot, then the lower strike); the floor's likewise.
  - **Confirmed ceiling, contests over value (Q6; the same rule as the King, F2.16, with W = `roll_switch_min_sec`):**
    - A board's leader leads from its `t_apply` until the next applied board's `t_apply`.
    - At each session's first applied board the confirmed ceiling is that board's leader. No switch until W of board time has passed.
    - At each applied board the confirmed ceiling switches if (a) it led less than `node_switch_incumbent_max_share` (0.25, owner rule, never tuned) of the trailing W, and (b) it is not the leader on the current board. It switches to the strike with the most lead time in that window (ties → the current board's leader; only candidates on the current board; a tie that does not include the current leader → nearer spot, then the lower strike, the F2.3 order, approved by the owner 2026-10-26). Known at that board's `t_apply`, never back-dated.
    - After a switch (price switches included) the next switch needs a full new window W.
    - A strike missing from a board counts as not leading.
    - A board gap longer than `ratio_gap_reset_sec` (10 s) on a 1-second day restarts the window, keeping the confirmed node. On a 1-minute-resolution day each board leads for its minute, so a switch needs the challenger to have led the full last minute and to lead the current board; any W under 60 s behaves as 60 s there.
    - So a pure tug-of-war between the confirmed node and one challenger (50/50) never switches, and a fading confirmed node never stays stuck while others trade the lead. **A clean switch happens at (1 − `node_switch_incumbent_max_share`) × W** (owner 2026-10-26: kept as coded): with W = 60 on 1-second boards a clean new leader is confirmed at the first board after the old one's share of the trailing 60 s falls under 25%, about 46 s after it took the lead. A tie in lead time that leaves out the current board's leader → nearer spot, then the lower strike (approved by the owner 2026-10-26).
  - **Price moves change the ceiling at once (Q9):**
    - At the first futures trade beyond the confirmed ceiling's upper edge the pause fires as below (if an episode is active, that node is the break node), and the ceiling is re-chosen at once as the current board's largest |value| among the candidates, the crossed node excluded until a trade below its lower edge. If one trade passes several ceilings, each is excluded in turn.
    - At resume (the first trade below the break node's lower edge), and whenever an exclusion ends, the ceiling is re-chosen at once the same way (the node is eligible again).
    - Both are **price switches**, never rolls.
  - Every switch is exactly one of three kinds:
    - a **roll-down**: a Q6 switch to a **lower** (closer) strike that was a ceiling candidate for the whole window W;
    - a **roll-back** (below): a Q6 switch to a **higher** strike, with an episode open, that was a candidate for the whole window W;
    - a **price switch**: a re-choice by price (above); a Q6 switch whose new strike was not a candidate for the whole window W, because it got above price by being crossed (e.g. a crossed floor now above price); and a switch to a higher strike with no episode. A price switch updates the confirmed ceiling and nothing else.
  - It doesn't matter whether the old ceiling faded, the new one grew, or both (owner 2026-10-05); how much each changed is recorded as a feature, against the last board on which the old ceiling still led (owner 2026-10-25, Q7).
  - **Floor roll-up** = the mirror image: a Q6 switch to a **higher** (closer) strike that was a floor candidate for the whole window W.
  - **Active roll, no timer (owner answer 2026-10-05, Q31):** "a ceiling roll-down signals bearish in general and a floor roll-up bullish; if the ceiling or floor rolls back to its original position, that's when you can discount it, but putting a timer on it doesn't make sense."
    - A ceiling roll-down starts a **roll episode**. Its **anchor** = the confirmed ceiling strike just before that first roll-down.
    - Further roll-downs during the episode extend it.
    - The episode is **active until rolled back**. A **roll-back** = a Q6 switch to a **higher** strike that was a candidate for the whole window W (price moving through a ceiling is a price switch, never a roll-back).
      - If the new strike is at or above the anchor, the episode ends (counted in `rollbacks_today`).
      - If it is below the anchor, it is a partial roll-back: the episode stays with fewer steps (counted in `partial_rollbacks_today`).
    - **Paused while price is through the current ceiling** (owner 2026-10-05: "if price trades through the most recent ceiling/floor, that should end it until price comes back below the ceiling or above the floor; account for the zones around the nodes"):
      - *Pause:* the first NQ trade (ES for S&P maps) above the upper edge of the current confirmed ceiling's mapped band (the full F1.4 band, strike ± `node_band` mapped with R, never the strike itself; `zone_proximity` plays no part; checked 2026-10-26, owner). That node is the **break node**; the ceiling is re-chosen at once (price switch, above).
      - *Resume:* the first later trade below the break node's lower edge, i.e. fully back under its zone. The episode resumes with the same anchor, and the ceiling is re-chosen at once (price switch, above).
      - Trading inside the band neither pauses nor resumes.
      - While paused, a roll-back still ends the episode, and the break node's band is the one in force at each trade.
      - **A new roll-down replaces a paused episode** (owner 2026-10-05): once price is through the old ceiling, a new ceiling forms above price; if that ceiling then rolls down, the paused episode ends and a **new, active episode** starts. Its anchor is the confirmed ceiling just before this roll-down. (Roll-downs during an active, unpaused episode still extend it.)
    - Episodes are intraday: they end at the session end (owner 2026-10-05, Q18: rolls are not measured across sessions).
    - Floor roll-ups mirror all of this. Both sides can be active at once.
  - **Target compression** (docs step 3: "upside targets compress"; owner: "if the upside or downside targets are shrinking that matters"). A feature, not a condition:
    - `above_mass_change_open` = Σ|v| of relevant nodes above spot now ÷ the same on the session's first applied board − 1;
    - `above_mass_change_roll` = the same ÷ the value on the last board before the active ceiling episode began − 1 (null with no active episode);
    - `below_*` likewise for the floor side.
- *Known at:* `t_apply` of the board that confirms the switch (rolls, roll-backs, price switches); the trade's `ts_event` (pause, resume); `t_apply` of each board (mass changes).
- *Outputs (per map):*
  - `rolling_ceiling` / `rolling_floor` = an episode is active and not paused;
  - `roll_steps` = the signed net strike positions (F0.10) from the anchor to the current confirmed ceiling (floor), + = toward price, negative allowed (owner 2026-10-25, Q8: −1 when a price switch while paused puts the ceiling one strike above the anchor), and the same in NQ/ES pts;
  - `roll_paused` (price is through the current ceiling / floor); `rolls_today`, `rollbacks_today` (full), `partial_rollbacks_today` and `roll_breaks_today` (pauses) per side;
  - the last roll's old-node shrink % and new-node growth % (against the last board on which the old node still led, Q7);
  - minutes since the episode began (an input the model weighs, not a timer that ends anything);
  - the mass changes.
- *Params:* `roll_switch_min_sec` (W; start 60 s, tuned at step 13 on months 1–4 by structure-model log loss, F16.4; owner 2026-10-25, Q29), `node_switch_incumbent_max_share` (owner rule, 0.25, never tuned), `ratio_gap_reset_sec`. Replaced: `node_switch_min_sec` (split into `roll_switch_min_sec` and `king_switch_min_sec`, Q29). Removed: `roll_window_min` (owner 2026-10-05: no timer), `roll_min_shrink_pct` and `roll_min_shift_strikes` (any confirmed switch to a closer strike is a roll, however it happened).
- *During a mapping-dislocation block of the map's chain* (F1.8; owner 2026-10-17, 2026-10-25): the futures-vs-band checks (pause, resume, the confirmed-ceiling exception, the re-choice by price) take no transition from trades against the blocked band; at the block end they are judged at once against the band then in force; the outputs are null inside the block.
- *Test (`tests/test_rolls.py`, W 60; prices in QQQ units, R = 1, bands ±0.5):* session from t = 0, spot 755; 760 (2.0e6, ceiling) and 759 (1.0e6). From t = 1: 760 → 0.8e6, 759 → 1.5e6 → ceiling roll-down at t = 60 (the first W of board time), shift 1 position, shrink 60%, growth 50% (against the t = 0 board, the last on which 760 led). QQQ spot 759.2 with NQ at 759.3, inside 759's band → 759 stays the ceiling. A trade at 759.6 → paused, break node 759, ceiling re-chosen at once (760 by the board's |value|, a price switch); a trade at 760.6 → 765, `roll_steps` −1; while above, 759/760 are excluded; spot back to 758.2 and a trade at 758.4 → resumed and 759 re-chosen at once (price switch), `roll_steps` 1. A floor 755 left above price (spot 754) and the largest there → confirmed after the old ceiling's share falls under 25%, as a price switch, not a roll-down (755 was not a candidate for the whole window). 760/759 alternating every 30 s → no switch, no roll; 759 leading 59 s of each minute → roll-down at t = 61. Partial and full roll-backs: 760 → 758 (roll), 759 (partial roll-back), 760 (roll-back, episode ends). The floor mirror: 750 → 751 roll-up, a trade below 751's lower edge pauses. A trade beyond inside a dislocation block → nothing until the block end. 1-minute day with W 30 → behaves as 60. Episode: anchor 760, roll-downs to 759 then 758 (`roll_steps` 2), a switch back to 759 (still active, steps 1) and to 760 → rolled back, episode ends; with no roll-back it is still active 3 hours later. Replacement: paused, the confirmed ceiling becomes 765 by a price switch and then rolls down to 763 (763 a candidate for the whole window) → the old episode ends and a new active episode starts with anchor 765.

### F2.12 Gamma regime (long vs short gamma)
- *Source:* §5.2 Gamma regime; `gamma-regime-positive.md` / `-negative.md` (positive regime = dealers long gamma, yellow predominates; negative = short gamma, purple predominates; "regime defines behaviour, not direction").
- *Definition:* per map at each applied board, over strikes k with |k − spot| ≤ `regime_band`% × spot:
  - `regime_net` = Σ v_k ÷ |v_King| (signed; > 0 = long gamma);
  - `regime_pos_share` = Σ_{v_k > 0} |v_k| ÷ Σ |v_k| ∈ [0, 1] (the docs' "predominance" of yellow);
  - `regime_long` = sign(`regime_net`) (categorical, for reports).
  Maps are never summed (§5.2); each is its own feature. The gameplan HVL is a separate gameplan input (F8).
- *Known at:* `t_apply` of each board.
- *Params:* `regime_band` (% of spot; owner 2026-10-05: 1).
- *Test:* a hand-built board gives a known net and share; a strike just outside the band is excluded.

### F2.13 Rug and reverse rug
- *Source:* `pattern-rug-setup.md`, `pattern-reverse-rug-setup.md`; **owner answer 2026-10-05** (FQ-04: "a positive node ceiling and then a negative node 1 strike underneath it, e.g. a positive QQQ 760 ceiling with a negative node at 759"). The SPEC placeholder's "no significant floor within `floor_search`" clause is not in the docs and is dropped.
- *Definition:* per map at each applied board (owner 2026-10-05: the negative node is **at most** 1 strike away; closer still counts; it has no size rule; the positive node must be relevant so that it actually causes a bounce):
  - **Rug** at node P when (i) P is relevant (≥ 25% of King, F2.4) and v_P > 0; (ii) at least one negative strike N lies below P at a price distance ≤ `rug_gap_strikes` × the map's strike step (F0.10; owner: 1), any size (value < 0; exactly 0 is not a partner, owner 2026-10-25, Q12); further negative strikes directly below N (contiguous = adjacent strike-list positions, F0.10, Q13) also count as partners ("one or more negative nodes"); (iii) spot < strike_P (the board's spot, F2.1).
  - **Reverse rug** at node P when (i) as above; (ii) a negative strike lies above P within the same distance, any size (contiguous negatives above it count too); (iii) spot > strike_P.
- *Outputs:* `rug` / `reverse_rug` on the node and as zone features (the zone holds P); `rug_neg_count`; `rug_neg_pct_sum` (Σ pct_of_king of the partners); `rug_first_partner_pct`. Model input only ("patterns are not signals", key takeaways).
- *Known at:* `t_apply` of each board.
- *Params:* `rug_gap_strikes` (owner: 1 strike step; fixed, not tuned).
- *Test:* ($1 strike step) P = 760 (+, relevant), 759 (−, 0.02 of King), 758 (−), 757 (+), spot 755 → rug with 2 partners (no size rule); spot 761 → no rug; 759 positive → no rug; a listed 759.5 (−) with 759 positive → rug (closer than one step); P at 0.20 of King → no rug; reverse rug: P = 750 (+, relevant), 751 (−), spot 752 → reverse rug. A 759 at exactly 0 → no partner; with no 758 listed, a negative 757 next to a negative 759 is contiguous. Mixed spacing (Q27): NDXP strikes every 10 with 5s near spot → step 10 on one board (a negative 10 below P is a partner) and 5 on another (it is not) (`features/rug.py`, `tests/test_lean_rug_trinity.py`).

### F2.14 Pika cloud
- *Source:* `pattern-pika-clouds.md`; **owner answer 2026-10-05** (FQ-06: "positive nodes right by each other, like QQQ 749, 750, 751; at least 10% the size of the King, and positive"; changed by the owner 2026-10-24: each a relevant node, below).
- *Definition:* per map, a **pika cloud** = a maximal run of consecutive strikes (adjacent positions in the board's strike list, F0.10) where every strike is positive and **relevant** (owner 2026-10-24, was pct_of_king ≥ 0.10): it enters at pct_of_king ≥ `pika_cloud_min_pct` (0.25, the same as `min_node_pct_of_king`) and keeps counting until it drops below `node_drop_pct` (0.20), against the same board's King, with the F2.4 identity and state (reset at each session's first board), so clouds don't flicker; and the run has at least `pika_cloud_min_nodes` strikes (owner 2026-10-05: 2). The cloud's band = [lowest member − b, highest member + b] (b = `node_band`), mapped with R. The cloud is context, not a level, and creates no events. Pika/barney colour (node sign, F2.5) is a separate feature and unchanged.
- *Outputs:* `in_pika_cloud` (Nasdaq maps: the NQ price inside a cloud band; **S&P maps: the cloud band ± `es_zone_proximity` contains the ES price**, the F2.19 Trinity rule, owner 2026-10-24), `pika_cloud_overlaps_zone` (Nasdaq maps: the cloud band overlaps the NQ zone), member count, Σ and max pct_of_king, cloud width (pts, EM), distance from price to the nearest cloud edge.
- *Known at:* `t_apply` (the cloud); t (`in_pika_cloud`).
- *Params:* `pika_cloud_min_pct` (owner: 0.25 since 2026-10-24, was 0.10; equal to `min_node_pct_of_king`), `node_drop_pct` (F2.4), `pika_cloud_min_nodes` (owner: 2). `pika_cloud_max_gap_strikes` is **removed**: members must be adjacent strikes (owner).
- *Test (owner 2026-10-24):* two adjacent positive strikes at 0.30 and 0.27 → a cloud of 2; 0.30 and 0.18 → no cloud (0.18 never entered); a strike that entered at 0.26 and is now 0.22 (with its neighbour at 0.30) → still a cloud; the same strike after it falls to 0.19 → no cloud; a strike at 0.30 that is negative → no cloud; a fresh strike at 0.22 → not a member. S&P map: a SPY cloud band [7,000, 7,002] with `es_zone_proximity` 1.25 and ES at 7,003 → `in_pika_cloud` true (at 7,003.5 false). The step-13 pika count (SPEC §12 step 13) reads `pika_cloud_overlaps_zone` (QQQ/NDXP) and `in_pika_cloud` (SPY/SPX) as computed here, never its own rule.

### F2.15 Air pockets, owner-approved 2026-10-04
- *Source:* `gamma-regime-negative.md` ("air pockets between nodes… get filled aggressively"); **owner answer 2026-10-05** (FQ-07: "a grouping of low-magnitude nodes that price can move through quickly; I look for nodes with less than 5k magnitude, and price usually moves quicker through negative ones").
- *Definition:* per map, an **air pocket** = a maximal run of consecutive strikes (F0.10) each with pct_of_king < `air_pocket_max_pct`, with no minimum length (a single strike can be a pocket; its size is already a feature; owner 2026-10-25, Q14) (owner 2026-10-24: **10%** of the King, on every map; was 2% from 2026-10-05, which replaced the absolute 5k since values are on different scales per map). These are small nodes that price travels through easily, not missing ones: they keep their values and count for every other feature as usual. Its band = [lowest strike − b, highest strike + b], mapped with R.
- *Outputs:*
  - `price_in_pocket`, and the size of the pocket containing price (strikes, pts, EM);
  - `pocket_ahead_size`: the size of the first pocket in the trade direction that starts within the gap to the next relevant node (0 if none); with no relevant node ahead (owner 2026-10-25, Q15): the first pocket that starts within 1 EM for the structure model, within the pair's target distance for the pair scorer, null if none;
  - `pocket_behind_size` (the stop side);
  - `pocket_neg_share` for each of those pockets = Σ|v| of its negative strikes ÷ Σ|v| of all its strikes (owner: price moves quicker through negative ones);
  - per map; Nasdaq maps on NQ against the NQ event, S&P maps on ES against the ES price, "ahead" = the NQ trade direction.
- *Known at:* `t_apply` (the pockets); t (price vs pocket).
- *Params:* `air_pocket_max_pct` (owner: 0.10, since 2026-10-24; was 0.02).
- *Test:* strikes 752–756 all below 10% of the King (one at 0.08: still in the pocket; a strike at 0.12 would end it), three of them negative carrying 80% of the run's |v| → one pocket of 5 strikes, `pocket_neg_share` 0.8; price at 754 → in the pocket.

### F2.16 King dominance and King migration, owner-approved 2026-10-04
- *Source:* `pattern-trend.md` ("a King Node far away from spot… as price trades toward it, it selects the node above it as the new King; trend days stair-step"), `gamma-regime-negative.md` ("a massive King Node that grows rapidly over time").
- *Definition, per map:*
  - `king_dominance` = |v_King| ÷ |v_2nd| (the second-largest |value|; null if that is 0 or absent);
  - `king_distance` = mapped confirmed King level − price (signed; pts, EM);
  - `king_velocity` = the velocity of the confirmed King strike (F2.8).
  - `king_dominance` uses the board's own largest |value| (F2.3, Q28), whichever strike is confirmed.
  - **Confirmed King (owner 2026-10-25, Q6/Q28):** the F2.11 lead-time rule with W = `king_switch_min_sec`; each board's leader = its largest |value| (F2.3, over the strikes present, ties nearer spot then the lower strike). At the session's first applied board the confirmed King is that board's leader; no switch until W of board time has passed; at each applied board it switches if (a) it led less than `node_switch_incumbent_max_share` (0.25) of the trailing W and (b) it is not the current board's leader, to the strike with the most lead time in that window (ties → the current leader; only strikes present on the current board); after a switch the next needs a full new window; a missing strike counts as not leading; a board gap > `ratio_gap_reset_sec` on a 1-second day restarts the window; on a 1-minute day W < 60 behaves as 60 and the challenger must have led the full last minute and lead the current board. Known at that board's `t_apply`, never back-dated (`features/node_switch.py`). A clean switch happens at (1 − `node_switch_incumbent_max_share`) × W (owner 2026-10-26), about 46 s with W = 60; both versions of the fading-750 test are kept (owner 2026-10-26).
  - Features (counting confirmed switches): `king_changes_today`, `last_king_shift_strikes` (signed: + = moved up), `minutes_since_king_change`, `king_stairstep_count` (consecutive confirmed changes in the same direction: 1 at the first change, reset to 1 on a direction change, 0 before any change; owner 2026-10-25, Q10).
- *Known at:* `t_apply` of the board that confirms a change.
- *Params:* `king_switch_min_sec` (W; start 60 s, tuned at step 13 on months 1–4 by structure-model log loss after `roll_switch_min_sec`, F16.4; owner 2026-10-25, Q29), `node_switch_incumbent_max_share` (owner rule, never tuned), `ratio_gap_reset_sec`.
- *Test (`tests/test_node_switch.py`, W 60, 1-second boards):* 760 vs 759 alternating every 30 s for 10 min → stays 760, Gatekeeper set unchanged; 759 leading 59 s and 760 1 s of each minute → switch to 759 at 10:01:01 (at 10:01:00 760 leads the board itself); a steady 70/30 split → no switch; King 750 fading from 10:00:00 while 759 and 760 swap every 30 s (759 first) → 759 at 10:00:46 (750's share under 25%; 759 30 s vs 760 16 s), and with the window starting 10:00:00 (after a 15-s board gap) → 759 at 10:01:00 by the tie → current leader; it stays 759 while they swap; no second switch within 60 s of a switch; a clean new leader → confirmed at the first board after the old King's share falls under 25% (46 s), the Gatekeeper set changes at that board, not earlier; session start → the first board's leader, no switch in the first 60 s; a missing strike counts as not leading; a 15-s gap restarts the window; on a 1-minute day the challenger must lead the full last minute and the current board; `roll_switch_min_sec` 30 and `king_switch_min_sec` 120 give different switch times on the same boards; stair-steps up, up, down → 1, 2, 1.

### F2.17 Rainbow road, owner-approved 2026-10-04
- *Source:* `pattern-rainbow-road.md` ("multiple prominent positive and negative nodes… no dominant nodes, no defined ceiling or floor… no-trade conditions"). Here it is a model input only, never a rule.
- *Definition:* per map at each applied board:
  - `n_pos_relevant`, `n_neg_relevant` (counts of relevant nodes by sign);
  - `sign_mix` = min(n_pos, n_neg) ÷ max(n_pos, n_neg) (0 when either is 0);
  - `king_dominance` (F2.16);
  - `floor_dist_em` / `ceiling_dist_em` = the distance from spot to the nearest relevant node below / above (null if none).
  - **Flag** `rainbow` = n_pos ≥ `rainbow_min_nodes_per_sign` and n_neg ≥ `rainbow_min_nodes_per_sign` and `king_dominance` ≤ `rainbow_max_dominance`.
- *Known at:* `t_apply` of each board.
- *Params:* `rainbow_min_nodes_per_sign`, `rainbow_max_dominance` (**NEW**; owner 2026-10-05: 2 and 1.5, fixed).
- *Test:* 3 positive + 3 negative relevant nodes with dominance 1.1 → rainbow; dominance 2.5 → not.

### F2.18 Whipsaw map, owner-approved 2026-10-04
- *Source:* `pattern-whipsaw.md` ("conflicting signals across indices… SPX bullish, QQQ bearish, SPY pinned… ranges still exist"), `trinity-confluence.md` (alignment vs divergence), `gamma-regime-negative.md`.
- *Definition:*
  - **Per-map bias** at t, from `lean` (F2.9): *up* if lean > `bias_deadband`, *down* if lean < −`bias_deadband`, else *pinned* (positive lean = accumulation above spot = up, one sign everywhere; owner 2026-10-25, Q2).
  - The trinity = SPY, SPX and the Nasdaq side, computed twice: with QQQ (as the doc names it) and with NDXP; both variants are separate features (owner 2026-10-05, FQ-12).
  - Features: `bias_<map>` (4); `trinity_bias_agree` = the largest number of trinity maps sharing one non-pinned bias (0–3); `trinity_bias_conflict` = the trinity holds both an *up* and a *down* map.
  - **Flag** `whipsaw` = `trinity_bias_conflict` and both a relevant floor and a relevant ceiling exist on the Nasdaq-side map ("ranges still exist", which separates it from F2.17); computed twice like the bias features, `whipsaw_qqq` and `whipsaw_ndxp`, each with its own map's floor/ceiling check (owner 2026-10-25, Q17).
- *Known at:* t (from `lean`, F2.9).
- *Params:* `bias_deadband` (**NEW**).
- *Test:* leans SPX +, QQQ −, SPY ≈ 0 → conflict true, agree 1; with NDXP + the NDXP trinity has no conflict, so `whipsaw_qqq` true and `whipsaw_ndxp` false (`tests/test_lean_rug_trinity.py`).

### F2.19 Trinity alignment
- *Source:* §5.2 Trinity; `trinity-confluence.md`; owner notes ("a QQQ floor or ceiling that SPY and/or SPX are also approaching at the same moment (on ES) makes the level a lot stronger").
- *Definition (overlap rule, owner 2026-10-24):* for an NQ event with role ρ touched at t0, evaluated at t ≥ t0 (the structure model at t = t0, the pair scorer at its `decision_ts`; data ≤ t only, the same code live and in backtest):
  - **SPY aligned at t:** at some moment τ ∈ [t0, t] (checked at every NQ trade, ES trade and board apply in the interval, with the last trades ≤ τ and the bands and F2.6 states as of τ) the NQ price was inside a relevant QQQ or NDXP node with role ρ (band ± `zone_proximity`) **and** the ES price was inside a relevant SPY node with role ρ (its own F2.6 state on ES, confirmed or provisional; a node with no role yet counts in ρ only if ES entered its band from ρ's side: from below for a ceiling, from above for a floor; owner 2026-10-25, Q19, Trinity only) whose band ± `es_zone_proximity` contained it; **and** that SPY node still holds ρ at t and has not been crossed against ρ since its overlap (owner 2026-10-25, Q18). If several SPY nodes overlapped, one that still counts at t is enough. Not "once true, stays true": a node crossed against ρ after its overlap stops counting.
  - **SPX aligned:** the same on SPX.
  - **Nasdaq side aligned:** at some τ ∈ [t0, t] the NQ price was inside any relevant QQQ **or** NDXP node with role ρ (band ± `zone_proximity`; it need not be a member of the event's own zone, owner 2026-10-25, Q20), and that node still holds ρ at t and has not been crossed against ρ since its overlap (Q18).
  - At t = t0 the window is one instant, so the structure model's values equal the earlier instant check.
  - Null for a map during a mapping-dislocation block of its chain (F1.8).
  - No King is required and no outcome is used.
  - `es_zone_proximity` defaults to `zone_proximity` × ES ÷ NQ at the session open (the first RTH trades).
- *Outputs (no new columns):* `trinity_count` (0–3: Nasdaq side + SPY + SPX, each by the overlap rule); `trinity_aligned` = `trinity_count` 3 (each map overlapped NQ at some moment in [t0, t], not necessarily the same moment; owner 2026-10-24, option (a)); `maps_with_node_here` (0–4: QQQ, NDXP, SPY, SPX each counted separately, by the same rule); smallest and mean pct_of_king of the aligned nodes, King/Gatekeeper among them and rug or pika structure among them, each taken from the aligned nodes **at their first overlap moment**; per-map `lean`.
- **Area events (owner 2026-10-26, SPEC §13 Q-areasp; F5.8):** no Nasdaq side, so `trinity_count`, `trinity_aligned`, `maps_with_node_here` and the aligned-node stats are null. Instead, per S&P map (pair scorer only, at `decision_ts`): `area_sp_aligned_spy` / `area_sp_aligned_spx` = at some τ in [visit start, `decision_ts`] ES was inside a relevant SPY (SPX) node's band ± `es_zone_proximity` in the event's role ρ (VAH ceiling, VAL floor) (τ is checked at the visit start and at every ES trade, NQ trade and board apply in the window, with the last trades, bands, relevance and F2.6 states as of τ, as for level events (owner 2026-10-26); so ES already inside at the visit start counts from it, and a node that becomes relevant while ES is inside counts from that board), with the rules above (Q18 provisional roles, Q19 unroled nodes from ES's entry side, null during a dislocation block of that chain); `area_sp_pct_of_king_<map>`, `area_sp_king_<map>` (confirmed King, Q28) and `area_sp_gatekeeper_<map>` of that node at its first overlap (several: the node that overlapped first **among those still counting at `decision_ts`** (holding ρ, not crossed against ρ), so one that overlapped first but has since failed is skipped; a tie → the larger pct_of_king; owner 2026-10-26); `area_sp_count` 0–2. During a dislocation block (F1.8) the level-event rule applies: the blocked map's columns are null, and `area_sp_count` is null while either S&P chain is blocked, as `trinity_count` is (F1.8 lists `trinity_count` among the null features, not counted with the blocked map as 0; owner 2026-10-26). `features/area_cross.py`, `tests/test_area_cross.py`. In ablation group 2.
- *Known at:* t (the overlap search over [t0, t]: each node's band from its map's last `t_apply` ≤ τ, prices from the last trade of each instrument ≤ τ, roles and crossed states from F2.6 as of τ and at t).
- *Roles that count (owner 2026-10-25, Q18; replaces FQ-23's "crossed nodes don't count"):* a node counts in role ρ if it holds ρ at t, confirmed or provisional (F2.6 step 5), and hasn't been crossed against ρ since its overlap. So a crossed ceiling in its provisional floor role counts for a long event (post-cross retests are not biased against), a crossed node never counts in the role it was crossed out of, a flipped node counts in its new role and a reclaimed one in its original role (`features/trinity.py`).
- *Test:*
  - ES inside a SPY ceiling band, SPX not, NQ inside a QQQ ceiling band → count 2, aligned false.
  - ES reaches the SPY band 8 s after the NQ touch while NQ is still inside its QQQ band → SPY false at the touch (structure model), true at a later `decision_ts` (pair scorer).
  - ES reaches the SPY band only after NQ has left its QQQ band → false.
  - ES reaches a SPY ceiling that is already crossed (provisional floor) → false for a short event, true for a long one (Q18).
  - A SPY node with no role yet: ES entered its band from above → counts as a floor; from below → doesn't count for a long event (Q19).
  - ES overlaps, then that SPY node is crossed against ρ before `decision_ts` → false at `decision_ts`.
  - Two SPY nodes overlapped, one crossed by `decision_ts` and one not → true.
  - SPY overlaps at t0 + 5 s and SPX at t0 + 20 s, never both at once → count 3 (with the Nasdaq side), aligned true (option (a)).
  - Node statistics: an aligned SPY node at 0.40 of its King at the first overlap and 0.30 at `decision_ts` → the size feature uses 0.40.
  - Causal: every value at t unchanged when the data after t is removed.

### F2.20 Nasdaq first, S&P obstacles in the path
- *Source:* §5.2 Nasdaq first; owner notes.
- *Definition:* for a pair with target T on NQ, the ES equivalent of T = T × (ES price ÷ NQ price) at `decision_ts`. **S&P path nodes** = relevant SPY/SPX nodes whose ES band lies strictly between the ES price and that ES target.
- *Outputs (pair features):* `sp_path_count`, `sp_path_max_pct`, `sp_path_has_king`, `sp_path_has_gatekeeper`, the distance to the first one (ATR). These path nodes also count as obstacles for target selection (F11.2).
- *Known at:* `decision_ts` (S&P bands from their last `t_apply`, prices from the last trades).
- *Test:* ES 7,000, ES target 7,020, SPY node bands at 7,010 and 7,030 → count 1.

### F2.21 Daily vol context (Tempest) and realized vs implied
- *Source:* §5.2 Daily vol context; §3.1 Tempest.
- *Definition:* for QQQ and SPY (and NDXP/SPX/VIX if served), the `/v1/vol/history` row of session T − `tempest_lag_sessions`: SVX 1d, 9d, 30d, 3m, 6m, ATM vol, term slope, 30-day risk reversal, butterfly, skew slope, call/put tilt. `rv_nq` = the annualised standard deviation of NQ RTH close-to-close log returns over the last `rv_lookback_sessions` completed sessions (roll-adjusted); `rv_iv_ratio` = `rv_nq` ÷ SVX30d(QQQ)/100. VIX (served; owner 2026-10-20, SPEC §13 Q-vix): the same vol fields as they are (`skew_s30` included) plus the row's `close` as Skylit's VIX reference price, not the official VIX close: it is the front VIX expiry's forward and rolls at each VX expiry, so its level is not an input; it enters only as `vix_ref_chg` = ln(close(T−1) ÷ close(T−2)), both rows from the same pre-open copy, null when session T−1 is a roll session (its row's `front_min` is larger than T−2's; the row's own field, no calendar, no parameter). No outside VIX source. These are inputs to the structure, day-type and pair models (not the trigger model).
- *Known at:* the session start.
- *Params:* `tempest_lag_sessions` (*measured*, step 7: the morning timings and the finality check of the pre-open copies, owner 2026-10-09), `rv_lookback_sessions`. Only Tempest copies pulled at or after the pre-open of a later day are read (the post-close row is a draft, SPEC §3.1 Tempest).
- *Test:* with T − 1 the row of the previous session is used, never session T's own row. `vix_ref_chg`: closes 20.0 (T−2) and 22.0 (T−1) with `front_min` 10,081 then 8,641 → ln(1.1) = 0.0953; the same closes with `front_min` 1,441 then 40,321 (T−1 a roll session) → null.

### F2.23 Multi-expiration context (owner 2026-10-24, SPEC §5.2, §13 Q-multiexp)
- *Inputs → known at:* the stored `/v1/historical` matrix snapshots at 09:25 and 10:00 ET (SPEC §3.1 Multi-expiration snapshots); a snapshot is **in force** from its instant + `mx_snapshot_delay_sec` (the live call is made then, with `at` = the instant, so backtest and live read the same answer). Each decision reads the latest snapshot in force: 09:25 until 10:00 + `mx_snapshot_delay_sec`, then 10:00. Flag `mx_snapshot` ∈ {0925, 1000}.
- *Cleaning:* expirations before the session date are dropped (a previous-close board still lists the expiry that ended that day); a root whose board `asOf` is before the previous session's 15:59 ET is null for that snapshot. Skylit's values differ in unit by root, so roots are compared only through signs and shares, never summed.
- *Buckets (by calendar days from the session date to the expiration):* **0DTE** = the session date; **1–7 days**; **8–45 days** (`mx_bucket_days` [7, 45]); later expirations are left out. NDX and SPX have no 0DTE (AM-settled monthlies only).
- *Definition, per side* (Nasdaq: QQQ, NDXP, NDX; S&P: SPY, SPXW, SPX):
  - per root r: n_r = Σ value over the 1–45-day buckets ÷ Σ |value| over the same cells ∈ [−1, 1]; z_r = the same over the 0DTE bucket (QQQ, NDXP, SPY, SPXW only);
  - `mx_net_sign_<side>` = sign(mean of n_r over the side's roots) (+1 long gamma, −1 short; 0 if exactly 0);
  - `mx_0dte_agree_<side>` = the share of the side's 0DTE roots with sign(z_r) = `mx_net_sign_<side>` ∈ {0, 0.5, 1};
  - `mx_0dte_share_<side>` = mean over the side's 0DTE roots of Σ|0DTE value| ÷ Σ|value| over all three buckets (LightGBM day-type challenger list only);
  - **walls:** per root and strike k, w_rk = |Σ value over the 1–45-day buckets at k| ÷ Σ |value| over the root's 1–45-day cells; the side's wall above (below) = the strike with the largest w_rk over the side's roots among strikes mapped above (below) price and within `mx_wall_reach_em` EM of it; mapped to NQ (Nasdaq: QQQ with QQQ's R, NDXP and NDX with NDXP's R, F1.7) or ES (S&P: SPY's R; SPXW and SPX with SPX's R) with the R in force at t. `mx_wall_above_dist_em_<side>`, `mx_wall_below_dist_em_<side>` = |mapped wall − price| in EM units at t; null if none within reach. Context only, never registry levels.
- *Outputs (structure model and pair scorer):* per side `mx_net_sign`, `mx_0dte_agree`, `mx_wall_above_dist_em`, `mx_wall_below_dist_em` (8 features) and `mx_snapshot`; `mx_0dte_share` only on the challenger list. Null during a mapping-dislocation block of the chain that maps the wall (F1.8) for the wall distances only.
- *Known at:* the snapshot's in-force time (signs, agreement, shares, wall strikes); t (wall distances).
- *Params:* `mx_bucket_days`, `mx_snapshot_delay_sec`, `mx_wall_reach_em` (owner rule and start values, not tuned).
- *Test:* a hand-built matrix (QQQ: 0DTE +5 at 100; 1–7 days −3 at 102 and −4 at 98; 8–45 days −2 at 105; a 60-day column +50 at 110) → n = −9/9 = −1, net sign −1, the 60-day column ignored; 0DTE sign +1 → agree 0; wall above = 102 (share 3/9 of the side's 1–45-day mass at that strike), below = 98; at 09:59 the 09:25 snapshot is read, from 10:00 + `mx_snapshot_delay_sec` the 10:00 one; an expired column dated the day before is dropped.

### F2.22 Flags and calendar context
- *Source:* §5.2 Flags; §1.2; §9 News blackouts.
- *Definitions:*
  - **Hedge nodes: dropped.** `docs/skylit/` doesn't define them; §5.2 says to drop them in that case.
  - `opex_week`: the session's week contains the monthly equity option expiration (third Friday; if that is a holiday, the trading day before).
  - `days_to_monthly_expiry`: trading days from T to that expiration.
  - `quarter_end_day`: T is the last trading day of Mar/Jun/Sep/Dec; `days_to_quarter_end` in trading days.
  - Release-day flags `cpi_day`, `fomc_day`, `nfp_day`, `ppi_day`, `retail_sales_day`: the calendar (as known at `preopen_start`) lists that release on T.
  - `half_day`: T has an `early_close_et`.
  - `minutes_to_close`: from t to the RTH close (16:00 or `early_close_et`).
  - `minutes_to_horizon_end`: from `decision_ts` to the earlier of the session flat-by and the next blackout-window start.
  - `in_session_release_today`: a `blackout: true` event whose window overlaps [09:30, entry_end).
  - `minutes_since_open` = t − 09:30.
- *Known at:* the session start (calendar), t (clocks).
- *Test:* FOMC at 14:00 → horizon end 13:29:30 (window start = 14:00 − 30 min − 30 s); `in_session_release_today` true.

---

## F3. Chart structure from trades (§5.3)

### F3.1 VWAP and σ bands
- *Definition:* for anchor A ∈ {18:00 ET (the CME-day start), 09:30 ET}, over the session's front-contract trades with `ts_event` ≥ A:
  - VWAP = Σ pᵢvᵢ ÷ Σ vᵢ;
  - σ = √(Σ vᵢpᵢ² ÷ Σ vᵢ − VWAP²) (volume-weighted standard deviation);
  - bands = VWAP ± 1σ and ± 2σ.
  It updates on every trade. Both anchors have 1σ and 2σ bands (owner 2026-10-05, FQ-09). Each line (VWAP and each band) is its own registry level; chart-level state machine F5.2.
- *Known at:* each trade.
- *Test:* three trades (100 × 1, 102 × 1, 104 × 2) → VWAP 102.5, σ = √(42,036 ÷ 4 − 102.5²) = √2.75 ≈ 1.658.

### F3.2 Volume profile and POC
- *Definition:* **daily** profile = volume per 0.25 price over trades from 18:00 ET of the CME day; **weekly** = from Sunday 18:00 ET, one contract only: in a roll week only the new contract's days, from the roll day (owner 2026-10-24; replaced the roll-shift on the roll day). Both are developing profiles; the only completed profile is the previous session's (F3.2b). Both develop and are recomputed at F0.1 profile instants. POC = the price with the largest volume; ties go to the price nearest the midpoint of the profile's high–low range (owner 2026-10-05, FQ-08); if still tied (equal distance above and below), the price nearer the **volume median** (the price with half the profile volume at or below it) wins (owner 2026-10-05); only an exact tie after that falls to the lower price.
- *Outputs:* daily and weekly POC levels; profile high/low (max/min traded price).
- *Known at:* each profile instant (F0.1).
- *Params:* `profile_update_sec`.
- *Test:* trades at 100.00 ×5, 100.25 ×3 before the 10:01:00 instant and 100.25 ×4 at 10:01:00.5 → POC 100.00 at 10:01:00 (the later trades wait for the next instant). A roll on Wednesday → the weekly profile from Wednesday's 18:00 open holds only the new contract's trades.

### F3.2b Previous-session profile (owner 2026-10-24, SPEC §5.3, §13 Q-amt)
- *Definition:* the completed volume profile of the previous session's CME day [18:00, 17:00) (the previous *session*: a Monday uses Friday's, a closed holiday is skipped), built from that day's own front contract only, with the F3.2 POC and F3.3 value-area rules. Known from that day's 17:00 close; used from the next 18:00.
- *Levels (registry sub-types):* `prev_poc` (level-type key `poc`), `prev_vah` and `prev_val` (key `va_edge`), each a chart level (F5.2: crossed on a settled 1-minute bar, post-cross retests and cross features F2.6/F2.6b; not sweep-eligible), band = price ± the key's half-width (F5.14). They live for the session. After a roll the previous session's profile is on the old contract: its prices are shifted by the roll spread (F0.4).
- *Outputs:* the three levels; `prev_poc_naked` = no trade at the POC price since the profile completed (from 17:00; turns false at the first trade there and stays false).
- *Test:* a previous day with trades 100.00 ×10, 100.25 ×6, 99.75 ×4 → POC 100.00, VA by F3.3; a trade at 100.00 at 09:31 → `prev_poc_naked` false from then; a roll day → the levels shifted by the roll spread, the volume never merged with the new contract's.

### F3.12 Previous-session value context (owner 2026-10-24, SPEC §5.3, §13 Q-amt)
Coarse and few; inputs of the structure model and the pair scorer:
- `open_vs_prev_va` ∈ {above, inside, below}: the first RTH trade at 09:30 against the previous session's VAH/VAL (F3.2b); known at 09:30.
- `dist_prev_poc_em`, `dist_prev_vah_em`, `dist_prev_val_em`: (price − level) ÷ EM (F0.8) at t, signed.
- `prev_va_overlap` = the overlap of today's developing daily value area (F3.3) with the previous session's, ÷ the narrower of the two widths ∈ [0, 1]; at each profile instant.
- `dpoc_drift_em` = (developing daily POC at t − the developing daily POC at the first profile instant ≥ 09:30) ÷ EM.
- `poc_migration_em` (owner 2026-10-24, the last addition before the freeze) = (developing daily POC as of t (F3.2, the latest profile instant ≤ t) − the previous session's POC (F3.2b)) ÷ EM, signed to the trade direction like the other directional features (+ = value moving with the trade; shorts: negated). At the touch (structure model) and at `decision_ts` (pair scorer). Ablation group 11.
- *Day type (owner 2026-10-24):* the 8 inputs stay (`ib_width_ratio` included; no swap test). On the LightGBM challenger list (SPEC §7.1 Retraining): `open_outside_prev_va_em` = the open's distance outside the previous value area ÷ EM, unsigned (max(open − VAH, VAL − open, 0) ÷ EM; 0 inside), `prev_va_overlap`, `dpoc_drift_em` and |`poc_migration_em`| (unsigned: the day-type label has no direction).
- *Params:* none new.
- *Test:* previous VA [100, 110]; open 112 → above; price 105 with EM 20 → `dist_prev_poc_em` per the POC; today's VA [106, 116] → overlap 4/10 = 0.4; developing POC 108 at 09:31 and 104 at 11:00 with EM 20 → drift −0.2; previous POC 103 → migration (104 − 103) ÷ 20 = +0.05 for a long, −0.05 for a short, 0.05 unsigned; open 112 → `open_outside_prev_va_em` (112 − 110) ÷ 20 = 0.1, an open at 105 → 0.

### F3.3 Value area, VAH/VAL zones (premium/discount)
- *Definition:* the value area = the set of prices built from the POC outward until ≥ 70% (owner) of the profile volume is included (owner 2026-10-05, FQ-08: expanded **one tick at a time**: at each step, add whichever of the next price above or the next price below has the larger volume; checked against Deepcharts at step 12. On a tie add both, and once one side reaches the profile edge keep adding the other side: owner-approved 2026-10-05, SPEC §13 Q40); VAH = its highest price, VAL = its lowest. **VAH zone** = [VAH, profile high]; **VAL zone** = [profile low, VAL] (§5.3). They are **not levels**: no event, no break, not stop or target candidates.
- *Outputs per profile (daily, weekly):* `in_vah_zone`, `in_val_zone`; `va_depth` = (price − VAH) ÷ (high − VAH) in the VAH zone, (VAL − price) ÷ (VAL − low) in the VAL zone (0 at the line, 1 at the extreme); the signed distance to the VAH and VAL lines (pts, ATR, EM); `va_direction_fit` = (short and in the VAH zone) or (long and in the VAL zone). They also scope area events (F5.8).
- *Known at:* each profile instant.
- *Test:* a hand-built profile with a known POC, VA, VAH/VAL; price at the midpoint of the VAH zone → depth 0.5.

### F3.4 LVNs
- *Definition (per §5.3):* at each profile instant, smooth the profile with a **Gaussian** kernel of σ = `lvn_smooth_ticks` ÷ 2 ticks (owner 2026-10-05, FQ-10; so ±1σ spans `lvn_smooth_ticks` ticks), truncated at ±`lvn_gauss_trunc_sigma` σ (owner-approved 2026-10-05, SPEC §13 Q42; start 3, which keeps 99.7% of the kernel's weight). An LVN is a local minimum m of the smoothed profile with s(m) ≤ `lvn_max_pct` × min(peak_left, peak_right), where peak_left/right = the highest smoothed volume between m and the next **LVN** (or the profile edge) on that side (§5.3). Because "next LVN" depends on which minima qualify, it is computed by iteration: start with every local minimum as a candidate; compute each candidate's peaks between it and its neighbouring candidates (or the edge); drop every candidate that fails; repeat until none is dropped.
  - Identity = (profile, round(price ÷ `lvn_merge_pts`) × `lvn_merge_pts`).
  - An LVN level appears when it first qualifies and ends when it no longer qualifies at a recompute.
  - Its band = price ± its chart-level half-width (F5.14).
  - HVNs are not used.
- *Known at:* each profile instant.
- *Params:* `lvn_smooth_ticks`, `lvn_gauss_trunc_sigma`, `lvn_max_pct`, `lvn_merge_pts`, `profile_update_sec`.
- *Test:* a two-hump profile with a valley at 30% of the smaller peak → one LVN; at 35% → none.

### F3.5 Single prints (TPO)
- *Definition (per §5.3):*
  - TPO periods are 30-minute blocks aligned to 18:00 ET (18:00, 18:30, …) of the CME day. A price bin (width `tpo_tick_pts`, bin = ⌊price ÷ `tpo_tick_pts`⌋) is *printed in* a period if any trade in that period falls in it.
  - At the close of each period, a **single-print run** = a maximal run of ≥ `sp_min_ticks` consecutive bins each printed in **exactly one completed period**. All bins of a run must come from the **same** period (owner 2026-10-05, FQ-11: the classic single print of one fast move).
  - The **unfilled part** = the bins of the run with no trade since the run was formed (trades in the current, incomplete period count as filling).
  - Each **contiguous** unfilled sub-run is its own level, band = [its lowest bin low, its highest bin high] ± `chart_level_half_width[single_print]` (learned, F5.14); `level_id` = run id (formation period + the run's lowest bin) + the sub-run's lowest bin. A sub-run survives however short it becomes (the SPEC ends the level only when the whole run is filled). A sub-run wider than `max_zone_width` is split into pieces of ≤ `max_zone_width` from the bottom up.
  - An event uses the band **frozen at its touch** for its audit, cross and label.
  - The level ends when every bin of the run has traded (`filled`).
  - Unfilled runs carry over to later sessions (roll-shifted) with `sp_age_sessions`.
- *Known at:* the period close (formation); each trade (filling).
- **Stacked single prints (owner 2026-10-24):** zone feature `sp_sessions_stacked` = the number of different formation sessions among unfilled single-print levels whose band overlaps the zone at t (test: today's run and one from two sessions ago both overlapping a zone → 2).
- *Params:* `tpo_tick_pts`, `sp_min_ticks`.
- *Test:* a 10-bin up-move printed only in the 10:00 period → one run at 10:30; a trade in the run's middle → two sub-run levels, each with its own `level_id`.

### F3.6 Opening range and IB high/low
- *Definition:* see F10 for the running range (the max/min RTH trade on the session's front contract (F0.4)) and the lock at 10:00 (`ib_lock_minute`). From the lock, **IB high** and **IB low** are registry levels (§5.3) and sweep-eligible. Before a break the IB high is a ceiling and the IB low a floor. Breaks, sweeps and flips follow F3.9 and F2.6 step 3 (a settled 5-minute bar beyond = flipped: a broken low acts as a ceiling, a broken high as a floor).
- **10:30 move (owner 2026-10-16):** at 09:30 + `ib_redraw_minute` (10:30:00), if a trade in [10:00, 10:30) exceeded the 10:00 IB high (low), that level is removed and the 09:30–10:30 high (low) becomes the IB high (low), a fresh level with no break history (role by the approach rule); a side not exceeded keeps its level and state. Same redraw as F10, so it follows the `golden_zone_redraw_1030` switch (owner 2026-10-26, SPEC §13 Q-fibvariants): with it off, the IB high/low locked at 10:00 stay all day.
- *Known at:* 10:00:00 (the levels exist from the lock); 10:30:00 (the move).
- *Test:* H 20,200 at 09:58, a new high of 20,210 at 10:00:05 → IB high stays 20,200 until 10:30:00, then moves to the 09:30–10:30 high (≥ 20,210) as a fresh level; the IB low stays.

### F3.7 Overnight and prior-period extremes
- *Definitions:*
  - **ON high/low** = the max/min trade in [18:00, 09:30) of D; levels from 09:30 (known then).
  - **PDH/PDL** = the max/min over the previous CME trading day [18:00, 17:00).
  - **Prior RTH high/low** = the max/min over the previous session's RTH.
  - **Prior week high/low** = the max/min over the previous week [Sunday 18:00, Friday 17:00).
  - All carried levels are roll-shifted (F0.4). All are sweep-eligible.
- *Test:* a holiday between sessions is skipped (the previous *session*, not the previous calendar day).

### F3.8 Retested extremes
- *Definition (per §5.3; candidates per FQ-13, owner 2026-10-05: the registry's session highs/lows — ON, PDH/PDL, prior RTH, prior week, IB high/low — of the last `retest_lookback_sessions` sessions):* a candidate extreme E from the last `retest_lookback_sessions` sessions (on the candidate list) becomes a `retested_extreme` level once a later touch came within `retest_tol_ticks` of E **and** that touch's structure label was *held* (resolved ≤ t). It is sweep-eligible; it is crossed and flipped like other highs/lows (F3.9, F2.6). It ends when crossed and not flipped back, or `retest_lookback_sessions` sessions after E's own session, whichever comes first (owner 2026-10-05, FQ-22). Only retests in sessions after E's own session count (FQ-22): that day E is already a registry level.
- *Known at:* the resolution time of the held touch.
- *Params:* `retest_tol_ticks`, `retest_lookback_sessions`.
- *Test:* a prior ON low of 100, a touch at 100.75 (3 ticks, within `retest_tol_ticks` 4) that resolves held → a `retested_extreme` level from that resolution time; a touch at 101.5 (6 ticks) → none.

### F3.9 Bounce, sweep or break (highs/lows only)
- *Source:* §5.3; owner notes ("sweeps at highs/lows are their own case").
- *Definition:* for a sweep-eligible level with band B and role ρ, from the first trade beyond B (the start of an **unresolved break**):
  - **Break (crossed)** at once if price trades > `sweep_max_depth` beyond B; or, if no reclaim happened by the close of the `sweep_reclaim_bars`-th 1-minute bar (owner 2026-10-05, FQ-14: bar 1 is the first full 1-minute bar after the bar in which price first traded beyond; that break bar's own close can already reclaim), at that bar's close.
  - **Swept (held)** when a 1-minute bar inside the sweep window **closes not beyond B** (inside B or on the range side) before a break.
  - After a break, F2.6 steps 3–5 apply (flip on a settled 5-minute bar; reclaim; post-cross retests, F2.6b); the cross back of a provisional role is again judged by this sweep rule.
  - While the break is unresolved, the far side neither re-arms the zone nor changes its approach side (§5.4 Touch and re-arm).
- *Outputs (pair columns as of `decision_ts`, F6.5):* `swept`, `sweep_depth` (pts, ATR), `sweep_seconds_beyond`, the sweep extreme (stop candidate 1/F11.1); later also the order flow during the sweep (F9).
- *Params:* `sweep_max_depth`, `sweep_reclaim_bars` (*tuned-13*).
- *Test:* a PDH at 100: trades to 105, the 1-minute close at 99.5 within the window → swept, depth 5; trades to 100 + `sweep_max_depth` + 0.25 → broke immediately.

### F3.10 Day-type inputs (for the day-type model, §7.1)
All are computed on RTH from 09:30 and known at t:
- `ib_width_ratio` = locked IB width ÷ the mean IB width of the last `ib_avg_sessions` sessions (null before 10:00).
- `vwap_crosses`: the number of 1-minute closes on the opposite side of the 09:30-anchored VWAP from the previous 1-minute close's side.
- `poc_crosses`: the same vs the developing daily POC (as of each close).
- `rotations`: a zigzag on trades. Track the current swing extreme in the current swing direction; when price moves ≥ `rotation_min_pts` × ATR against it, count one rotation, reverse the direction and start the new swing from that extreme.
- `time_above_vwap_frac`: the fraction of RTH seconds with the last price above the 09:30 VWAP.
- `new_highs`, `new_lows`: the number of 1-minute bars that set a new RTH high/low.
- `range_vs_em`: the RTH range so far ÷ the expected move.
- `regime_*` (F2.12) and the vol context (F2.21).
- **Direction-free forms** (the day-type label has no direction, so the logistic model gets inputs that grow toward "trend" either way; proposed in part 2, F15):
  - `vwap_side_share` = max(`time_above_vwap_frac`, 1 − `time_above_vwap_frac`) ∈ [0.5, 1];
  - `new_extreme_skew` = |`new_highs` − `new_lows`| (many new highs and few new lows, or the reverse, = one-sided).
- *Params:* `ib_avg_sessions`, `rotation_min_pts`.
- *Test:* a hand-built price path with 3 known zigzag rotations at ATR 100 and `rotation_min_pts` 0.25; `time_above_vwap_frac` 0.2 → `vwap_side_share` 0.8; 7 new highs and 2 new lows → `new_extreme_skew` 5.

---

### F3.11 Expected-move (sigma) levels (owner 2026-10-14, SPEC §13 Q-sigma)
- *Definition:* for session T, row = the pre-open `/v1/vol/history` row of session T − `tempest_lag_sessions` (QQQ for NQ, SPY for ES) and C = the NQ RTH close of session T − 1 (the last front-contract trade at or before the RTH close, roll-shifted to today's front contract; ES: the ES close). m = `em_1d_pct` ÷ 100 of the row, as stored, never recomputed (Tempest's own one-day move in calendar time: SVX1d × √(minutes from that close to the next session's close ÷ 525,600), so ÷ √365 on a Monday–Thursday row, × √3 on a Friday row, early closes included; the row's `front_min` is not always that span, SPEC §13 Q-svxwknd). The same m is F0.8's expected-move unit (Q-svxwknd answered 2026-10-22). The levels are C × (1 + k·m) for k ∈ `em_level_sigmas` ({−2, −1, +1, +2}; owner rule, never tuned), fixed for the whole session (they do not move intraday), so they match Tempest's chart lines (`/v1/vol/cones` `levels[day]`). With `tempest_lag_sessions` = 2 the row's own move spans the wrong day, so m = SVX1d of that row ÷ 100 × √(calendar days from the T − 1 close to the T close ÷ 365) instead (the same calendar-time rule).
- NQ lines are registry levels (sub-types `em_dn2`, `em_dn1`, `em_up1`, `em_up2`; level-type key `em_level`, F5.14): the chart-level state machine F5.2 (crossed = a settled 1-minute candle beyond the band, flipped = a settled 5-minute bar), half-width `chart_level_half_width[em_level]` frozen at step 13 from the reaction-distance report. Not sweep-eligible (computed lines like the VWAP bands, not traded highs/lows). ES lines are cross-market features only (F0.9).
- *Outputs:* the levels as zone members; `running_sigma` = (price − C) ÷ (C·m), signed; the distance to the nearest line above and below (pts, ATR, EM); which line (k) a zone member is.
- *Known at:* the pre-open job (the row is pulled at or after 08:30 ET, SPEC §3.1 Tempest; C is known at T − 1's close).
- *Missing row:* no lines that session (never a stale row), as for the other vol-context features.
- *Params:* `em_level_sigmas` (owner rule); `chart_level_half_width[em_level]`, `chart_level_half_width_cap[em_level]`, `flip_margin[em_level]` (F5.14, F15).
- *Test:* C = 20,000, `em_1d_pct` 0.80 → ±1σ 19,840 / 20,160, ±2σ 19,680 / 20,320; a Friday row with SVX1d 15.4381 and `tempest_lag_sessions` = 2 → m = 15.4381 ÷ 100 × √(3 ÷ 365) = 1.400% from Friday's close to Monday's.
- *Live check:* `nqbot step7 sigma-check` compares Tempest's live `day` lines (`/v1/vol/cones`) with C × (1 ± k·m) rebuilt from the stored row (QQQ/SPY prices; the NQ conversion is the bot's own), on three sessions (SPEC §13 Q-sigma; done 2026-10-20/10-22: method, anchor and symmetric ±2σ confirmed). From step 22 the pre-open job logs the gap between Tempest's `day` lines (once they show today's set) and the bot's from the row; the cones are never an input.

## F4. Dark-pool levels (§3.1, §5.4)

- *Definition:* per ticker (QQQ, SPY), at session T: from the raw prints (`/v1/dark-pool/trades`) with trade `timestamp` ≤ 16:00 ET of session T − `dp_lag_sessions` and ≥ that cutoff − `dp_lookback_days` calendar days, take the `dp_top_n` prints with the largest `notional` (ties: later timestamp first).
  - Identity = (ticker, print timestamp, price).
  - Band = price ± `dp_band` (QQQ $), mapped with the current R like a strike (F1.1).
  - A level exists for every session in which it is on the list and ends when it leaves the list.
  - **QQQ** levels are NQ registry levels; **SPY** levels are ES cross-market levels.
  - `dp_partial` = the lookback reaches before the first day of pulled history, or before `dp_history_start` (2026-04-01, Skylit's first dark-pool date; step 7, 2026-10-06).
  - **Owner 2026-10-06: not in the first model.** `dp_in_model: false`: no dark-pool levels in the registry or events and no dark-pool features (QQQ levels, SPY cross-market levels) in the first model, because development months 1–6 have no dark-pool data; the prints are still pulled and stored, and F4 is added and tested at a later retrain once enough training months have it.
  - Chart-level state machine (F5.2): crossed only on a settled 1-minute candle.
- *Known at:* the session start (pre-open).
- *Outputs:* level, `dp_notional_rank`, `dp_age_days`, `dp_pct_avg_vol` (the print's `pctAvgVol`).
- *Params:* `dp_top_n`, `dp_lookback_days`, `dp_band` (owner confirms at step 13), `dp_lag_sessions` (*measured*, step 7: 1, late-print check 2026-10-22, no print or top-3 list changed on 5 mornings).
- *Test:* a print timestamped 16:00:01 on T − 1 is excluded at `dp_lag_sessions` = 1; a print 46 days old is excluded.

---

## F5. Zones, events and confluence (§5.4, §5.4b)

### F5.1 Level registry
- *Source:* §5.4 (the canonical list).
- *Definition:* every NQ registry level is a record {`level_id`, type, sub-type (e.g. `pdh`, `vwap18_up1`, `qqq_node`), instrument, band [lo, hi] at t, role, state, `created_ts`, multi-day flag}.
- **NQ registry types:**
  - GEX nodes: relevant QQQ/NDXP nodes (F2.4).
  - Opening range: the golden zone (F10, a container) and IB high/low (F3.6).
  - Session highs/lows: ON, PDH/PDL, prior RTH, prior week, retested extremes (F3.7–F3.8).
  - Volume profile: daily/weekly POC and LVNs, single prints (F3.2–F3.5); the previous session's POC, VAH and VAL (F3.2b).
  - VWAP: the 18:00 and 09:30 lines and bands (F3.1).
  - Expected-move levels: ±1σ and ±2σ around the previous RTH close (F3.11).
  - QQQ dark pools (F4).
  - Gameplan zones (F8, containers).
- **Sweep-eligible:** session highs/lows and IB high/low.
- **Chart levels:** every type except GEX nodes, the golden zone and gameplan zones.
- **ES cross-market levels:** SPY/SPX relevant nodes, SPY dark pools, ES ON high/low, ES PDH/PDL and the ES expected-move lines (from SPY, F3.11). They run on the same machinery on ES but never create NQ events.
- **`level_id`:**
  - node = map + strike;
  - chart level = type + its session (e.g. `pdh-2025-11-18`);
  - LVN = profile + rounded price;
  - single print = formation period + lowest bin;
  - dark pool = the F4 identity.

### F5.2 Chart-level crossed / flipped / reclaimed
- *Definition:* as F2.6, with one difference (owner, §5.4): a level is **crossed** when a 1-minute bar settles beyond its band (F0.5). Trading beyond the band without settling is not a break, and an audit or touch in progress stays valid.
- Applies to VWAP lines and bands, POC, LVNs and dark pools.
- Single prints are judged against the band frozen at the touch and end when filled. Highs/lows apply F3.9 first.
- Flip, reclaim and the post-cross retests work as in F2.6 steps 3–5, with the cross features of F2.6b; a chart level's cross back is a settled 1-minute bar beyond B on the original side.
- *Test:* VWAP ceiling band [100, 101]. Trades reach 101.5 but the bar closes at 100.75 → not crossed. The next bar opens at 101.25 and closes at 101.5 → crossed at that bar's close.

### F5.3 Zone merge
- *Source:* §5.4 Zone object → Merge.
- *Definition:* containers (golden zone, gameplan zones) never merge. All other **active** NQ levels (not crossed, or crossed and armed in their provisional new role, F2.6 step 5) merge into zones as follows.
  1. Two levels **qualify** to merge when their bands overlap (touching counts) or their centres are ≤ `confluence_tol` apart.
  2. **Recompute** on every batch of updates sharing one timestamp (a board, a profile instant, a VWAP tick, a level created, ended or crossed):
     - (a) existing zones keep their still-valid members;
     - (b) a member stays only while it still qualifies with another kept member and the zone width (the union of the bands) stays ≤ `max_zone_width`. Otherwise the member farthest from the zone's **founding anchors** (the centre of the founding members' bands) is released first, and the check repeats;
     - (c) released and new levels are processed in ascending band-centre price. Each joins the nearest qualifying zone (nearest by centre distance) if the merged width stays ≤ `max_zone_width` (ties: the zone holding the larger pct_of_king, then the older zone); otherwise it founds a new zone;
     - (d) zones that now qualify with each other merge if the width allows; the older `zone_id` survives.
  3. The result depends only on the set of levels at that timestamp, never on arrival order.
- *Params:* `confluence_tol`, `max_zone_width`.
- *Test:*
  - A three-level chain A–B–C where A–C would exceed `max_zone_width` → zones {A, B} and {C}.
  - The same input in shuffled order → the same zones.
  - A merge of two existing zones keeps the older id.

### F5.4 Zone edges, per-member invalidation and end states
- *Definition:*
  - **Edges** = the union of the active members' bands [min lo, max hi] at t.
  - When a member is crossed by its own rule (F2.6, F5.2, F3.9), it leaves the active members and the zone carries on with the rest. Once armed in its provisional new role (F2.6 step 5) it is active again on the new side and merges by F5.3 like any other level.
  - **Zone crossed** = every **current** member is crossed by its own rule (§5.4): the moment the zone's last active member is crossed, counting members that joined after the touch; members that ended without crossing (e.g. dropped below `node_drop_pct`) leave without counting as crossed. This is "broke" in the structure label (F11.4).
  - A zone that loses all its members without any crossing (e.g. its nodes fall below `node_drop_pct`) ends; for an open event that is a structure-label timeout.
- *Test:* zone {QQQ node, PDH}. Price trades past the node band (the node is crossed) but is reclaimed within the PDH sweep window → the zone is not crossed, and the node is no longer an active member.

### F5.5 Zone identity
`zone_id` is assigned when a zone is created: a short id `z<n>`, unique per session, with a lookup table to its founding anchors. It is kept while any member remains; members joining or leaving don't change it. Freshness and re-arm counts attach to the `zone_id` and to each member's `level_id`.

### F5.6 Touch and re-arm
- *Definition:*
  - **Touch band** = [lo − `zone_proximity`, hi + `zone_proximity`]. **Re-arm band** = [lo − (`zone_proximity` + `rearm_dist`), hi + (`zone_proximity` + `rearm_dist`)].
  - A zone is **armed** (re-armed) at the first trade outside its re-arm band after it was last disarmed. The **approach side** = the side price was on at that re-arm (§5.4 Direction) and doesn't change while the zone stays armed. If an update moves the zone across price without a touch (price never inside the touch band), the zone is disarmed and re-arms on the new side at the next trade outside its re-arm band. A zone created on top of price, or moved onto it, stays unarmed until then.
  - A **touch** = the first trade inside the touch band while armed; the zone is then disarmed.
  - **Unresolved breaks:** while a member's break is unresolved, trades on the far side neither re-arm the zone nor change its approach side, and coming back inside is the same event. A break is unresolved when:
    - a high/low is inside its sweep window and within `sweep_max_depth`;
    - a chart level is beyond its band with no settled 1-minute bar yet;
    - the golden zone is beyond 0.79 with no settled bar yet.
  - After a cross, a level in its provisional new role (F2.6 step 5) arms on the crossed side, with or without a flip; after a reclaim, on the original side.
- *Params:* `zone_proximity`, `rearm_dist`.
- *Test:* zone [100, 102], `zone_proximity` 2, `rearm_dist` 10. Price 120 (armed, from above) → 104 (touch) → 110 (no re-arm) → 115 (re-armed) → 103.5 (second touch).

### F5.7 Events, IDs and direction
- *Definition:*
  - **Event** = one touch of a zone, at the touch trade's `ts_event`.
  - `event_id` = `<UTC timestamp at second resolution, e.g. 20251119T155200Z>-<zone_id>`.
  - `order_id` = `<event_id>-<style><n>-<account>-<leg>` (§5.4).
  - **Direction:**
    - approach from below → short (ceiling); from above → long (floor);
    - crossed (provisional) and flipped levels use their current role;
    - golden zone: from the fib orientation (F10);
    - gameplan zones: the approach rule, with agreement with the gameplan `side` as a feature (F8).
  - **Event tags:** `in_golden_zone` (the zone is the golden-zone container), `minutes_since_open`, `tradeable` (a pair-level filter on `decision_ts`, §1.2; also false inside a mapping-dislocation block of a chain in the zone, F1.8).
- *Test:* the event-id format, and that two firings of style (b) on the same event get different order ids.

### F5.8 Area events (VAH/VAL, from step 19)
- *Source:* §5.4 Area events.
- *Definition:*
  - **Creation:** a style (a) or (b) detector fires (F9) inside a VAH zone (shorts only) or a VAL zone (longs only), daily or weekly, while no active level zone's touch band contains the price.
  - `event_type` = `area`; `zone_id` = the containing area(s) + session. Each area event is its own touch group.
  - Later firings in the same area before re-arm are firings n = 2, 3, … of that event, subject to `trig_min_gap_sec`.
  - The event ends when price leaves the area or re-arm is met.
  - The next area event in the same area needs `area_rearm_sec` since the last trigger **or** a move of `rearm_dist` from the last trigger price.
  - A trigger inside a level zone's touch band belongs to that level's event instead.
  - **Role (owner 2026-10-26, SPEC §13 Q-areasp):** a VAH-area event (short) has ρ = ceiling, a VAL-area event (long) ρ = floor.
  - **Visit start:** the moment NQ last entered the area on this visit; with NQ inside both the daily and the weekly area at the event's first firing, the earliest moment since which NQ has been continuously inside at least one of the areas containing it at that firing. The cross-market window is [visit start, `decision_ts`], data ≤ `decision_ts` only.
  - **Overlap check:** τ is checked at the visit start and at every ES trade, NQ trade and board apply in the window, with the last trades, bands, relevance and F2.6 states as of τ, as for level events (owner 2026-10-26) (F2.19).
  - **Cross-market features (pair-scorer inputs only, at each pair's `decision_ts`; the trigger model has no cross-market inputs and the structure model's prediction is null):** `es_at_level` (F5.16, area rule) and the `area_sp_*` columns (F2.19, area events); `trinity_count` / `trinity_aligned` are null.
  - **Other inputs (owner 2026-10-26):** **null** (they describe a level zone's own members): stack count, member-type flags, members' node type (King / Gatekeeper / significant, F2.5), node sign (pika/barney, F2.2), node size, % of King and freshness (F2.3, F5.11), rug and reverse rug (F2.13), `pika_cloud_overlaps_zone` (F2.14), beach-ball and cross/flip state of members (F2.6, F2.6b, F2.7), zone width, member spread, distance from the zone centre, and the zone's Trinity (`trinity_count` / `trinity_aligned`). **Computed as for level events** (map-wide or price-based): gamma regime (F2.12), velocity lean and bias (F2.9, F2.18), whipsaw, rolling ceilings/floors (F2.11), King dominance and migration (F2.16), rainbow road (F2.17), magnets (F2.10), air pockets (F2.15), `in_pika_cloud` (F2.14), S&P path features (F2.20), `smt_div` / `rel_delta` / `rs_nq_es` (F5.16), day type, vol context, VIX, calendar, time, `rvol_today`, VAH/VAL depth, previous-session value context and all order flow; the distance to the next level zone above and below is not null: measured from the price at `decision_ts` (pts, ATR, EM). Features the owner's list doesn't name, placed by the same test (a level zone's own members → null; map-wide or price-based → computed): computed: `map_age_sec` and `map_resolution_1m` (F2.1, map-wide), multi-expiration context (F2.23, map-wide; wall distances from price), day memory (F5.12, session-wide), the per-map `lean` (F2.19 output = F2.9), and the pair's target-side features (target-node velocity, confluence at target: they describe the target, not the entry zone); `es_stack_count` and the ES member types (F5.16) come from the ES level found by the area rule (they describe the ES zone, not an NQ zone); null: members' relevance and velocity (F2.4, F2.8), the rest of the F5.13 makeup (`king_present`, `gatekeeper_present`, largest pct_of_king, container makeup, distance to the near edge, `distance_to_level_center`), zone widths (F5.14, a level's band), `maps_with_node_here` and the aligned-node stats of F2.19 (they need the Nasdaq side); not an input: the approach features (F5.18: structure-model inputs, reaching the pair scorer only through the structure probability, which is null here); not features: F5.15 (a report) and F5.17 (simulator ties).
- *Params:* `area_rearm_sec`, `trig_min_gap_sec`, `rearm_dist`.
- *Test:* a firing in the VAH zone with the nearest zone 30 pts away → area event; a firing 2 s later is suppressed by `trig_min_gap_sec`. Cross-market (owner 2026-10-26, `tests/test_area_cross.py`; the rule in `features/area_cross.py`, the builder at step 19): ES enters a SPY King band acting as a floor while NQ is in the VAL area → `area_sp_aligned_spy` true, `area_sp_count` ≥ 1, `es_at_level` true; that SPY node crossed down before `decision_ts` → false; ES at it only before the visit started → false; ES at its ON low with no SPY/SPX node → `es_at_level` true, `area_sp_count` 0; SPY and SPX both aligned → 2; NQ enters the weekly VAL area at 10:00 and the daily at 10:05, first firing 10:08, ES at a SPY floor node at 10:02 → counts (visit start 10:00). Owner follow-up 2026-10-26: ES already inside the SPY band before the visit starts and still inside at the visit start → counts; a SPY node becoming relevant while ES is inside its band → counts from that board; SPY node A overlaps 10:01 and is crossed by 10:04, node B overlaps 10:03 and holds, decision 10:05 → the columns come from B; SPX blocked → `area_sp_aligned_spx` and `area_sp_count` null.

### F5.9 Containers
The golden zone (F10) and gameplan zones (F8) never merge. Levels whose bands overlap a container are its **confluence members**: counted as features, not merged. A container has its own touch, re-arm, events and invalidation: the golden zone per F10; a gameplan zone when price trades past its far edge (§7.1); its `invalidation` is a feature only (F8, owner 2026-10-07). After its cross a gameplan zone follows F2.6 step 5 (provisional opposite role, retests from the far side, cross features F2.6b, `flip_margin` 0); its cross back is price trading past its other edge (owner 2026-10-23).

### F5.10 Touch groups
- *Definition:* events whose touches happen on the same trade (same `ts_event` and sequence, e.g. a container and a level zone inside it) share a `touch_group_id`.
- Training weights follow §5.4: a pair row gets 1 ÷ (events in the group × filled pair rows of its event); a trigger row gets 1 ÷ firings in the event.
- *Test:* a golden zone containing a POC zone, touched by one trade → 2 events, 1 group.

### F5.11 Freshness
- *Source:* §5.4 Freshness; Skylit docs ("tap degradation: fresh nodes matter more").
- *Definition:* per level (`level_id`) and per role:
  - `tests_so_far` = touches of zones containing the level (today, and since creation for multi-day levels);
  - `holds_so_far` = those whose structure label resolved *held* with resolution time ≤ t;
  - `pending_tests` = touches not yet resolved;
  - `minutes_since_last_test`.
- Zone features: min, max and mean over members, plus the zone's own test count (by `zone_id`).
- *Test:* touch 1 at 10:00 resolves held at 10:20. At a touch at 10:10, `holds_so_far` = 0 and `pending_tests` = 1.

### F5.12 Day memory
At t, among today's events whose structure labels resolved ≤ t: `held_today`, `broke_today`, `timeout_today` and the held rate. Also `yesterday_day_type`, the previous session's EOD day-type label (F11.6).

### F5.13 Confluence and proximity features (zone makeup)
Computed as of the touch for events and as of `decision_ts` for pairs:
- `stack_count` (active members), and a one-hot count per type (§5.4 registry with sub-types: daily vs weekly profile, VWAP anchor and band, node map QQQ vs NDXP).
- Per member: node pct_of_king, node type and freshness. Largest pct_of_king; `king_present`; `gatekeeper_present`.
- Geometry: zone width; member spread (max − min member centre); each member type's distance from the zone centre; distance from price to the near edge; `distance_to_level_center` (pts, and as a fraction of the zone width, §5.4b); distance (pts, ATR, EM) to the next zone above and below.
- Container makeup (the levels inside the golden zone or a gameplan zone).
- Context: VAH/VAL (F3.3), Trinity (F2.19, by its overlap rule over [touch, t]: at the touch for events, over [touch, `decision_ts`] for pairs; owner 2026-10-24), and the rug, pika and other map patterns (F2.13–F2.18) of the zone's node members.
- *Test:* a zone with a QQQ King and the weekly POC → `stack_count` 2, `king_present` true, one-hot counts correct.

### F5.14 Zone widths (chart levels) and reaction distance
- *Source:* §5.4b.
- *Level-type keys* (used by `chart_level_half_width`, `chart_level_half_width_cap` and `flip_margin` in `features.yaml`). Each registry sub-type (F5.1) maps to one key:

| Key | Registry sub-types |
|---|---|
| `vwap` | the 18:00 and 09:30 VWAP lines |
| `vwap_band` | their ±1σ and ±2σ bands |
| `em_level` | the ±1σ and ±2σ expected-move lines (F3.11) |
| `poc` | daily and weekly POC; the previous session's POC (`prev_poc`) |
| `va_edge` | the previous session's VAH and VAL (`prev_vah`, `prev_val`; owner 2026-10-24) |
| `lvn` | daily and weekly LVNs |
| `single_print` | single-print sub-runs |
| `on_extreme` | ON high, ON low |
| `pdh_pdl` | PDH, PDL |
| `prior_rth` | prior RTH high/low |
| `prior_week` | prior week high/low |
| `ib` | IB high/low |
| `retested_extreme` | retested extremes |
| `qqq_node`, `ndxp_node` | GEX nodes (`flip_margin` only; their band is `node_band`) |
| `dark_pool` | QQQ dark pools (`flip_margin` only; their band is `dp_band`) |

  Daily and weekly versions, and the two VWAP anchors, share one key: owner-approved 2026-10-05, SPEC §13 Q41. The sub-type stays a model feature (F5.13), so the model can still tell them apart.
- *Definition:* each chart-level type has a half-width `chart_level_half_width[type]` ≤ `chart_level_half_width_cap[type]`. It is frozen at step 13 from the reaction-distance report on months 1–4 (NQ). ES levels default to the NQ value × ES ÷ NQ at the open.
- **The measurement:**
  - **Approach:** after a re-arm (price beyond cap + `zone_proximity` + `rearm_dist` from the level line), price comes within cap + `zone_proximity` of the line.
  - **Reaction:** a reversal of ≥ `react_pts` from the approach extreme within `react_minutes`.
  - **Reaction distance:** max(0, the deepest penetration past the line before the reaction), taken over reacted approaches only. For highs/lows, penetration beyond the sweep window is excluded. For single prints it is measured from the near edge of the unfilled run.
  - **Half-width:** the `react_width_q` quantile of reaction distances per type, clipped to the cap.
- Excluded: the golden zone, gameplan zones, GEX nodes (they use `node_band`) and dark pools (`dp_band`).
- *Params:* `chart_level_half_width`, `chart_level_half_width_cap` (per type), `react_pts`, `react_minutes`, `react_width_q`.
- *Test:* five hand-built approaches with penetrations {0, 1, 2, 3, 10}, where the last one doesn't react → the quantile is taken over {0, 1, 2, 3}.

### F5.15 Flip behaviour (count report at step 13, test at step 15)
Owner 2026-10-23 (SPEC §12 steps 13 and 15, §13 Q-flipretest). Step 13, count only (events, never outcomes; months 1–4): crosses, reclaims, 5-minute flips and post-cross retests before vs after a flip, with the post-cross bucket sizes per level-type group (GEX nodes, chart lines, highs/lows, gameplan zones), any bucket under 100 flagged. Step 15, pre-declared: the structure model with the F2.6b features against the 5-minute rule alone on the post-cross retests of months 1–6 (SPEC §12 step 15); if it fails, the retests and the cross features are removed (F2.6 step 5 reverts) and nothing else changes.

### F5.16 Cross-market features (§5.4)
Computed at the touch and at `decision_ts`:
- **ES at its own stacked level (overlap rule, owner 2026-10-24, as F2.19):** `es_at_level` at t = at some moment τ ∈ [t0, t] the NQ price was inside the event's zone touch band and the ES price was inside an ES zone's touch band (± `es_zone_proximity`) in the same role ρ as the NQ event, at the same moment, and that ES zone still counts at t: a member holds ρ at t, confirmed or provisional (F2.6 step 5, post-cross retests), and hasn't been crossed against ρ since its overlap (owner 2026-10-26, the F2.19 Q18 rule; its members by their own rules, F5.4; one overlapped ES zone that still counts is enough). ES zones are the ES cross-market levels merged by F5.3. Features (no new columns): `es_at_level`, and `es_stack_count` and the ES member types of that ES zone at its first overlap moment. At the touch (structure model) the window is one instant.
- **Area events (owner 2026-10-26, SPEC §13 Q-areasp; F5.8):** the same `es_at_level` column, pair scorer only, at `decision_ts`: true if at some τ in [visit start, `decision_ts`] (τ is checked at the visit start and at every ES trade, NQ trade and board apply in the window, with the last trades, bands, relevance and F2.6 states as of τ, as for level events (owner 2026-10-26)) ES was inside an ES level zone's band ± `es_zone_proximity` in the event's role ρ (VAH ceiling, VAL floor), and that level holds ρ at `decision_ts` (confirmed or provisional) and hasn't been crossed against ρ since its overlap (the Q18 rule). The same ES level set (dark pools out while `dp_in_model` is false); `es_stack_count` and the ES member types from that ES zone at its first overlap. In ablation group 2.
  - *Test:* ES enters an ES PDH zone 10 s after the NQ touch with NQ still in its zone → `es_at_level` false at the touch, true at a later `decision_ts`; the ES PDH crossed against the event's role (by the sweep rule) before `decision_ts` → false; ES reaches it only after NQ has left its zone → false; an NQ post-cross retest (long, a crossed QQQ ceiling in its provisional floor role) with ES at an ES level that was also crossed upward and holds the floor role provisionally → counts (owner 2026-10-26; `tests/test_lean_rug_trinity.py`).
- **ES reacting there:** dropped (owner 2026-10-05, FQ-15: Trinity alignment and `es_at_level` already cover ES at the level; the focus is NQ).
- **NQ/ES divergence (SMT):** at t, NQ's high (low) over the last `smt_lookback_min` is a new high (low) versus its prior `smt_lookback_min` window while ES's is not, or the reverse. Feature: `smt_div` ∈ {none, nq_only, es_only}, per side.
- **Relative delta** (owner 2026-10-05, FQ-16: keep, the model learns its relevance): over the last `rel_delta_sec`, `rel_delta` = (NQ delta ÷ NQ volume) − (ES delta ÷ ES volume) (each in [−1, 1], so the two markets' sizes don't matter), and `delta_sign_agree` = the two deltas have the same sign. Known at each second.
- **NQ vs ES relative strength (owner 2026-10-16, B5):** for k ∈ {5, 15} minutes, `rs_nq_es_k` = (NQ(t) ÷ NQ(t − k) − 1) − (ES(t) ÷ ES(t − k) − 1), in %, where each price is the last trade at or before that time on the front contract of the session; signed so that + = NQ stronger in the trade direction (shorts: negated). Computed at the touch (structure model) and at `decision_ts` (pair scorer). Null if either window would mix two contracts (§5.1 Same contract) or has no trade. Ablation group 2 (cross-market). Every one of them is in the step-11 no-lookahead shift test (owner 2026-10-16).
- *Params:* `smt_lookback_min`, `rel_delta_sec` (both **NEW**); the B5 windows {5, 15} min are fixed (owner 2026-10-16).
- *Test:* NQ makes a new 15-minute high while ES stays 2 pts below its prior high → `smt_div` = nq_only (high side). NQ buy 600 / sell 400, ES buy 300 / sell 500 → `rel_delta` = 0.2 − (−0.25) = 0.45, `delta_sign_agree` false.

### F5.17 Taps between map updates, and ties
- *Definition (§5.4b):* every trade is checked against every band, in exchange sequence; bands change only at updates.
- **Simulator ties:** when two of entry, stop, target and breakeven fall in the same 1-second bar, the cached trades of that second are replayed in sequence. A single trade print that meets two conditions at once is scored pessimistically and flagged `ambiguous_tick`:
  - entry + stop → filled and stopped;
  - entry + target → filled, target not yet reached;
  - stop + target → stopped.
- *Test:* each of the three single-print cases.

### F5.18 Approach (structure model; owner 2026-10-16, B4)
- *Definition:* at the touch t, with prices = the last NQ trade at or before each time (pre-09:30 Globex trades count, so a 09:45 touch has full values; front contract of the CME day, never two contracts: null if a window would mix them, §5.1), ATR = the F0.8 value known at the touch, and + = moving toward the zone (approach from below: rising; from above: falling):
  - `approach_move_k` = the price change over (t − k, t] in the approach direction ÷ ATR, for k ∈ {1, 15} minutes;
  - `approach_range_k` = (max − min trade over (t − k, t]) ÷ ATR, for k ∈ {1, 15} minutes;
  - `approach_straightness_15` = |p(t) − p(t − 15 min)| ÷ Σ|p(t − 15 + i) − p(t − 16 + i)| over the 15 one-minute steps i = 1…15 (prices sampled at each whole minute back from t; null if the sum is 0).
- 5 columns, structure-model inputs (the pair scorer gets them through the structure model's probability). Ablation group 9 (approach). Every one of them is in the step-11 no-lookahead shift test (owner 2026-10-16).
- *Known at:* the touch.
- *Test:* a short touched at 20,100 after price rose from 20,040 in 15 minutes in steady 4-point steps, ATR 200 → `approach_move_15` 0.3, `approach_straightness_15` 1.0; the same net rise with a 20-point dip and recovery inside → straightness 60 ÷ 100 = 0.6.

---

## F6. Entries (§5.5)

### F6.1 Failed audit (short) / audited hold (long)
- *Definition:* within an event, after ≥ 1 trade inside the zone's band (the band itself, not the touch band) since the last re-arm, the audit **completes** at the close of the first 1-minute bar that closes **outside the band on the approach side** (short: close < band lo; long: close > band hi). That bar is the **trigger bar**.
- There is no depth or time limit. The audit dies only when the zone itself is crossed (F5.4).
- It must complete before re-arm. A re-arm during the trigger bar itself doesn't cancel it: re-arm takes effect only after that bar closes.
- *Outputs (pair columns, as of `decision_ts`):* `audit_depth` (the deepest point into or past the band, in pts and as a fraction of the width) and `audit_seconds_inside`.
- *Test:* ceiling [100, 102]. A trade at 101 at 10:00:20; the 10:00 bar closes inside the band at 100.5; the 10:01 bar closes at 99.75 → the audit completes at 10:02:00 with trigger bar 10:01.

### F6.2 Sweep reclaim (style (c) trigger at highs/lows)
- *Definition:* during a sweep (F3.9), the trigger is the first 1-minute close **on the range side** of the band (beyond the band edge on the approach side) within `sweep_reclaim_bars`. A close inside the band keeps the level reclaimed (swept) but doesn't trigger.
- *Test:* PDH ceiling [100, 100.5]. A sweep to 104; a bar closes at 100.25 (inside: swept, no trigger); the next bar closes at 99.5 → trigger.

### F6.3 Settled bar
See F0.5.

### F6.4 Entry styles and `decision_ts`
| Style | `decision_ts` | Available from |
|---|---|---|
| (a) absorption | each absorption firing (F9.5) | step 19 |
| (b) other order-flow trigger | each firing of exhaustion, delta divergence or footprint imbalance (F9.4, F9.6, F9.7); same-second firings of different (b) detectors form one decision point | step 19 |
| (c) audit / sweep reclaim | the trigger bar's close (F6.1, F6.2) | Phase D |
| (d) plain | the touch (F5.6) | Phase D |

The owner's ranking a = b > c > d only breaks near-ties in EV (`style_tie_ev_r`, §7.1).

### F6.5 Pair-only columns
These values are measured after the touch: `swept`, `sweep_depth`, `sweep_seconds_beyond`, `audit_depth`, `audit_seconds_inside`, `fib_level_reached` (F10). Each is computed as of the pair's `decision_ts` and stored only in `pairs.parquet`. They are null for style (d) and for the structure model (§7.1).

---

## F7. Entry order types and fill rules (§5.5b)

**Reference prices and limits:**
- *Passive limit:* at the zone edge (style d) or the order's limit.
- *Stop-limit:* the trigger is 1 tick beyond the trigger bar's extreme in the trade direction (style c), or 1 tick beyond the current price (style a option); the limit is the trigger + `max_entry_slip_ticks` ticks.
- *Marketable limit:* the reference is the best ask (buy) / bid (sell) at send; the limit is the reference + `max_entry_slip_ticks` ticks through it.

**Arrival** = `decision_ts` + `order_latency_sec`.

**Fills (labels and simulators):**
- *Passive limit:* fills only on a trade **through** the limit after arrival (no queue model).
- *Stop-limit:* working from arrival. It triggers on the first trade at or through `stopPrice` (or at arrival if price is already through), then fills like a marketable limit at that trade's time, never worse than `limitPrice`. Any unfilled rest becomes a resting limit at `limitPrice`.
- *Marketable limit:* fills at the `mbp-1` best ask (bid) at arrival up to the displayed size, the remainder at the cap; anything beyond the cap is unfilled. The size is the §6.1 default-risk mix in NQ-equivalent contracts, rounded up.

**Exits:** a target counts only when price trades ≥ 1 tick through it. A stop fills on touch, at the stop ± `exit_slippage_ticks`; the breakeven stop is treated the same way.

**Time-to-live and removal.** These are the only removal reasons:
- `entry_ttl_sec` expiry;
- zone crossed (golden zone: settled past 0.79, which also flips it; gameplan zone: traded past its far edge, F5.9); golden zone redrawn at 10:30 (F10);
- zone vanished, or moved more than `entry_reprice_tol` from the entry price;
- start of a news blackout;
- data outage;
- `!pause`;
- area event: price left the area;
- `entry_end`.

Each removal takes effect `order_latency_sec` after it occurs; a fill before then counts.

*Params:* `entry_ttl_sec`, `entry_order_type` per style (*exec*, frozen at step 13 or 19), `max_entry_slip_ticks`, `exit_slippage_ticks`, `order_latency_sec`, `entry_reprice_tol`.

*Test:* one case per order type, including a stop-limit partial fill and a target that is touched but not traded through.

---

## F8. Gameplan features (§5.6)

- *Inputs → known at:* `data/processed/gameplans/<date>.json`, known at the session start (written before 09:30, §3.4). With no gameplan, every gameplan feature is null.
- *Definitions:*
  - **Gameplan zone:** a container [`zone`[0], `zone`[1]] with its `side`, `stop`, `targets`, `invalidation` and `es_gates` (parser version 2, §5.6). A single-price zone [p, p] is a zero-width band (its touch uses `zone_proximity` like any level). A conditional entry (acceptance / breakout / breakdown, parser v3) has an empty `zone`: it uses the single-price zone [p, p] at `trigger.nq_price` like any other single-price zone, with its own `side`, stop and targets (owner 2026-10-07); with no `trigger.nq_price` either, it creates no zone. `other_conditions` is not read by any feature yet. It is crossed when price trades past its far edge (F5.9); its `invalidation` is a feature only, never a trading rule (owner 2026-10-07, CLAUDE.md rule 6).
  - `in_zone`: the event's touch price, or the price at `decision_ts`, lies inside a gameplan zone.
  - `zone_side_agrees`: that zone's `side` matches the trade direction.
  - **Stated flips (owner 2026-10-23; from the parser version of the step-20 re-parse):** where the text states a flip (e.g. "lost → becomes resistance"), the parser records it on the zone as written (`stated_flip`: lost / reclaimed, the new role, the timeframe; never inferred). `gp_stated_flip_agrees`: on a post-cross retest of that zone (F5.9), the stated new role matches the event's direction; null if none is stated. Off with the other gameplan inputs until the gameplan check.
  - **Scenario set used (owner 2026-10-07):** the `ny_am` set while `decision_ts` is inside the NY AM window (09:30 to `gameplan_ny_am_end`, start 12:00, the window the gameplans write), the `session` set otherwise; when the gameplan has only one set (`ny_am`, `session` or `unspecified`), that one at any time. If the matching set is missing but the other exists, the features are null.
  - `scenario_agrees`: some scenario in the set used has a `bias` that matches the trade direction (`neutral` and `mixed` never match). Also `scenario_prob_agree` = Σ prob of the agreeing scenarios; null if any scenario in the set has no probability.
  - `es_gate_ok`: every ES gate of the zone holds at `decision_ts`; null if the zone has no gate or any gate is unclear. Direction (owner 2026-10-07): `above`, or wording "holding" / "reclaiming" X → ES above X; `below`, or "losing" / "rejecting" / "failing" X → ES below X; any other wording stays unclear (null, never a guess). A gate given as a range (or two prices) holds when ES is beyond the whole range in its direction, fails when beyond the whole range the other way, and is null inside the range.
  - `hvl` and `hvl0`: two separate features, never one picked for the other (owner 2026-10-07): each the signed distance from price to the level's nearer edge (pts, ATR), 0 inside; also `above_hvl` / `above_hvl0`. Null when the gameplan doesn't give it. `regime_line` (a range): signed distance from price to its nearer edge, 0 inside; null unless the gameplan labels one.
  - `gameplan_invalidated` (owner 2026-10-07): the zone's invalidation as written has been met between the session start and `decision_ts`, per its timeframe: `1m_close` = a 1-minute bar closing beyond the price; `settled_bar` = a 1-minute bar settling beyond it (§5.5, F0.5), or a bar of the length the gameplan names; `acceptance` = a bar of the stated minutes (`acceptance_minutes`, 15 if not given; bars labelled by their start, F0) closing beyond it; `not_stated` = a 1-minute bar settling beyond it (the §5.2 chart-level default); `other`, an ES-only invalidation or no price = null. A zone with several invalidations: met if any is met; null if none can be computed. No invalidation = null.
  - **Targets (owner 2026-10-07):** a target given as a range is a band like any other level: reaching its near edge counts as reaching it, and candidates and order follow the band rule (near edge and centre, F11.2; order by the middle). A target with no price is not a candidate.
  - `gameplan_member`: the event's zone overlaps or contains a gameplan zone.
- *Gating (§5.6):* these inputs enter a model only when its training months hold ≥ `gameplan_min_days` gameplan days and the gameplan check has passed. Otherwise they are omitted, and gameplan stop/target candidates are encoded as `other_price`.
- *Test:* a gameplan with zone E1 sell [30640, 30722] and an ES gate below [7766, 7766]. A short event at 30650 with ES at 7760 → in zone, side agrees, gate OK. A gate "ES holding 7,776" with ES at 7,780 → OK; "ES stalling under 7,740" → null. A gameplan with `ny_am` S-A down 0.5 / S-B up 0.5 and `session` S-A down 0.4 / S-B neutral 0.6: a short at 10:15 → agrees, prob 0.5; at 13:00 → agrees, prob 0.4. HVL [30590, 30590] and HVL0 [30600, 30600] with price 30620 → hvl +30, hvl0 +20. Invalidation `acceptance` 15 min above 30722: a 15-minute bar closing at 30725 → invalidated; only a 1-minute close there → not.

---

## F9. Order flow (§5.7; built at step 17, used from step 19)

### F9.1 Scope and firing rule
- *Definition:*
  - Order flow is **in scope** while the NQ price is inside an active level zone's touch band or a VAH/VAL area (F3.3). Out of scope, nothing is computed into events.
  - Every detector is evaluated **once per second**, at each whole second s, on records with `ts_event` ≤ s.
  - A detector **fires** when its own condition is true. Each firing writes one trigger row, at most once per `trig_min_gap_sec` per detector per event.
- *Params:* `trig_min_gap_sec`.
- *Test:* the condition is true at seconds 10, 11, 12 and 17, with a gap of 5 → firings at 10 and 17.

### F9.2 Big trades (input only, never a trigger)
- *Definition:* the fills of one aggressive order are combined into one trade: same `ts_event`, side and price, plus same-side prints within `big_trade_agg_ms` of the first. A combined order is **known at** its first print + `big_trade_agg_ms`; a detector evaluated at second s uses only orders complete by s. A big trade = a combined size ≥ `big_trade_min_contracts`, where that threshold is, per `big_trade_slot_min` (30-minute) RTH slot, the 99th percentile of combined NQ order size in that slot over the prior `big_trade_lookback_sessions` full sessions (F9.10's session rules), clipped to [`big_trade_slot_floor`, `big_trade_slot_cap`] (0.5, 2.0) × the whole-session 99th percentile (known at the session start). Time of day only, not today's volume (owner 2026-10-17, B1).
- *Outputs:* count, size and side near the zone; where they hit relative to the zone; price progress (ticks) over the following `abs_seconds` (known only once that window has passed).
- *Test:* prints 30 + 25 at the same `ts_event`, side and price, plus 20 same-side 30 ms later (agg 50 ms) → one order of 75, known at first print + 50 ms; at threshold 70 it is a big trade.

### F9.3 Delta, CVD, CVD slope
- *Definition:*
  - **Delta** over an interval = buy-aggressor volume (side B) − sell-aggressor volume (side A); side N is ignored.
  - **CVD** = cumulative delta from 18:00 ET of the CME day.
  - `cvd_slope` = the OLS slope of per-second CVD over the last `cvd_slope_sec` seconds (contracts per second).
- *Known at:* each trade (delta, CVD); each second s (slope).
- *Params:* `cvd_slope_sec` (**NEW**).
- *Test:* trades B 5, A 2, N 4, B 1 → delta +4; CVD rising 2 contracts per second for `cvd_slope_sec` → slope 2.0.

### F9.4 Footprint and diagonal imbalance (style b)
- *Source:* §5.7; **owner answer 2026-10-05** (FQ-17: "usually just buyer dominance or seller dominance causes an imbalance; I think this is learned in the order-flow model"). So the data decides the window and the stack size, inside the step-19 grid.
- *Definition:* the footprint over the rolling window (s − `imb_window_sec`, s] gives, for each price p, the ask volume A(p) (buy aggressors) and the bid volume B(p) (sell aggressors).
  - **Buy imbalance** at p: A(p) ≥ `imb_ratio` × B(p − 1 tick) and A(p) ≥ `imb_min_volume` × scale(s, `imb_window_sec`) (F9.10; owner 2026-10-17). When B(p − 1) = 0, only the volume floor applies.
  - **Sell imbalance** at p: B(p) ≥ `imb_ratio` × A(p + 1 tick), likewise.
  - The detector **fires** (style b) when ≥ `imb_stack_min` consecutive prices carry imbalances in the trade direction (sell imbalances for shorts). `imb_stack_min` = 1 means a single imbalance; the step-19 grid includes 1.
  - Inputs (every second in scope, firing or not): imbalance counts per side, the longest stack per side, the max ratio.
- *Known at:* each second s.
- *Params:* `imb_window_sec` (**NEW**), `imb_ratio`, `imb_min_volume` (**NEW**), `imb_stack_min` (**NEW**); all *tuned-19*.
- *Test:* A(101) = 30, B(100.75) = 8, ratio 3 → buy imbalance at 101; with imbalances at 101, 101.25, 101.5 and `imb_stack_min` 3 → fires (long).

### F9.5 Absorption (style a)
- *Source:* §5.7; owner notes: "lots of buyers at the top and price starts pushing down off them"; "I like to see price start to push away from the absorbed area before entering"; absorption stop "about 5 points past the absorbed area".
- *Definition (short at a ceiling; longs mirrored):* at second s, an **absorption range** exists when all three hold:
  - (i) buy-aggressor volume in (s − `abs_seconds`, s], traded within `abs_ticks` ticks of the visit's running high (the price where the buying stalls), is ≥ `abs_volume` × scale(s, `abs_seconds`) (F9.10; owner 2026-10-17). That high may be anywhere in the zone's touch band, at any depth (owner 2026-10-05, FQ-27: "I wait until price enters a zone of interest, then look for order-flow indications anywhere in the zone"). Area events use the area's running extreme the same way.
  - (ii) no trade in (s − `abs_seconds`, s] exceeds the event's running high as of s − `abs_seconds` (no new extreme);
  - (iii) best-ask refills (F9.8) counted **over the event so far** (touch to s; §5.7 says "event-level") are ≥ `abs_refills`.
- The range = [lowest, highest] price of that absorbed buy volume.
- The detector **fires** at the first second at which price has traded ≤ the range low − `abs_push_ticks` ticks. There is no time limit (owner answer 2026-10-05, FQ-18: "it's not really a how-long thing… seeing buyers or sellers get absorbed and then price move away from that point"): the range stays valid until the event ends or its zone is crossed; the seconds from detection to the push are an input, so the models learn whether a slow push matters.
- *Outputs:* absorption flag and range (they feed stop candidate 5, F11.1), absorbed volume, refills, and `absorbed_and_exhausted` (exhaustion or delta divergence also fired in the same zone visit).
- *Params:* `abs_volume`, `abs_ticks`, `abs_seconds`, `abs_refills`, `abs_push_ticks` (*tuned-19*; the grid includes 0).
- *Test:* price 10 pts deep inside a ceiling zone; 400 contracts bought within 2 ticks of that visit high over 20 s, a flat high, 5 refills, then a trade 4 ticks below the range → fires (depth in the zone doesn't matter).

### F9.6 Exhaustion (style b)
- *Definition (short; longs mirrored):* at second s, both hold:
  - (i) a new event high was made in (s − `exh_lookback_sec`, s];
  - (ii) buy-aggressor volume in (s − `exh_lookback_sec`, s] ≤ `exh_decay_ratio` × the buy volume in (s − 2·`exh_lookback_sec`, s − `exh_lookback_sec`].
- Thin volume at the extreme ("unfinished auction") is **an input only, not a firing condition** (owner answer 2026-10-05, FQ-19: "one of the many tested order-flow inputs"): `extreme_volume` = the volume traded at the event's extreme price, ÷ the mean volume per price in the visit, and `extreme_two_sided` = both bid and ask volume traded there.
- *Params:* `exh_lookback_sec`, `exh_decay_ratio` (*tuned-19*).
- *Test:* lookback 10 s, ratio 0.5: buy volume 200 in the earlier 10 s, 80 in the last 10 s, with a new high 3 s ago → fires; 120 in the last 10 s → doesn't; no new high → doesn't.

### F9.7 Delta divergence (style b)
- *Definition (short; longs mirrored):* at second s, the price high in (s − `div_lookback_sec`, s] exceeds the visit's previous high, set at time t₁ inside the lookback, by ≥ `div_min_pts`, while CVD(s) ≤ CVD(t₁).
- It fires style (b). When an absorption range exists in the same visit, it also sets `absorbed_and_exhausted` and makes stop candidate 5 available. It never fires style (a).
- *Params:* `div_lookback_sec`, `div_min_pts` (*tuned-19*).
- *Test:* a high of 100 at CVD 500, then a high of 101 at CVD 480 within the window, `div_min_pts` 1 → fires.

### F9.8 Top of book (`mbp-1`)
- *Definition:* at t, from the latest `mbp-1` record:
  - `spread_ticks`;
  - `l1_imbalance` = (bid size − ask size) ÷ (bid size + ask size);
  - **refill** at the best ask = an update that raises the ask size at an unchanged best-ask price after a trade at that price since the previous update (mirrored for the bid);
  - **pull** = a size decrease at an unchanged best price with no trade at that price in that update.
- Refills and pulls are counted per side over `tob_window_sec`, and separately over the absorption window (F9.5).
- *Params:* `tob_window_sec` (**NEW**).
- *Test:* a hand-built `mbp-1` sequence with 2 refills and 1 pull.

### F9.9 Other trigger-model inputs (§7.1)
- `seconds_since_touch` = `decision_ts` (or the firing second) − the event's touch time; for area events, since the event's first firing.
- `aggressive_no_progress` = aggressive volume in the trade's counter-direction over the last `abs_seconds` ÷ (1 + the ticks price progressed in that aggressor's direction over the same window) (the "aggressive volume vs no-progress" input). Known at each second s.
- *Test:* 300 buy-aggressor contracts with a 1-tick rise over the window → 150.
- **Sequence inputs (owner 2026-10-16, B2; ablation group 8, order-flow extras):** at a trigger row at second s, within the event since its touch (area events: since its first firing), each null when it never happened:
  - `secs_since_absorption`, `secs_since_imbalance`, `secs_since_exhaustion`, `secs_since_divergence` = s − the last second at which that detector's condition held in the trade direction (F9.4–F9.7; absorption: an absorption range existed, F9.5), so a detector firing now gives 0 for itself;
  - `secs_since_big_trade` = s − the known time of the last big trade (F9.2, known at its first print + `big_trade_agg_ms`), and `big_trade_side_rel` ∈ {with, against} the trade direction;
  - `distinct_detectors_fired` = how many of absorption, imbalance, exhaustion and divergence have fired in this event up to s (this firing included);
  - `abs_then_imb` = an imbalance in the trade direction fired within `seq_window_sec` (30 s, fixed, never tuned; owner 2026-10-16) after an absorption range was detected in this event, at or before s.
  - Not built (owner 2026-10-16): the first detector's type and the full order of events.
- **Short-term range (owner 2026-10-16, B3; group 8):** `range_5m_ticks` = (max − min NQ trade over (s − 300 s, s]) ÷ tick size; `range_5m_vs_session` = `range_5m_ticks` ÷ the median range of today's completed 5-minute bars from 09:30 (09:30–09:35, 09:35–09:40, …; null before 09:40). Same contract only (null if a window would mix two, §5.1).
- Every one of them is in the step-11 no-lookahead shift test (owner 2026-10-16).
- *Params:* `seq_window_sec` (features.yaml, fixed).
- *Test:* absorption detected at 10:00:00, a buy-side imbalance (for a long) at 10:00:20 → `abs_then_imb` true; at 10:00:40 → false; at the 10:00:20 firing `secs_since_absorption` 20 (if the range last existed at 10:00:00), `distinct_detectors_fired` 2. A 5-minute high–low of 20,110–20,080 → `range_5m_ticks` 120.

### F9.10 Volume baselines and multi-window order flow (owner 2026-10-17, B1; ablation group 8; `features/volume_norms.py`)
- *Normals:* N(m) for each ET RTH minute m (09:30 … 15:59) = the median NQ volume in that minute over the prior `vol_baseline_sessions` (20) full sessions. Early closes, holidays and sessions the §3.2 damage rule drops are left out. Each prior session's volume comes from its own front contract (the series the detectors read; one contract per CME day from 18:00, so no RTH window mixes two; null if one would). With fewer than 20 such sessions every feature below is null, never built on fewer. Computed by the pre-open job from prior sessions only; the backtest calls the same functions per session (rule 4). Warm-up: NQ trades from 2025-09-08 (§3.2), so 2025-10-06 is the first session with 20, and every session with Skylit boards (from 2025-10-07) has them. Minute of day in ET (daylight-saving changes line up).
- `vol_tod_w(s)` = volume in (s − w, s] ÷ Σ_m N(m) × (seconds of minute m inside the window ÷ 60): each minute's normal pro-rated by how much of it the window covers, so a part-finished minute is never compared with a full minute's normal; null if the window leaves RTH.
- `rvol_today(s)` = Σ min(V_m, `rvol_clip_mult` × N(m)) ÷ Σ N(m) over the minutes since 09:30, the current minute pro-rated on both sides (`rvol_clip_mult` 5): each minute is clipped before it is added, so one news spike can't inflate the rest of the day. From 09:30 only; no overnight volume feature (owner). An input of the trigger model, the structure model (at the touch) and the pair scorer (at `decision_ts`); not of the day-type model (its 8 inputs stand; §13 Q-dtrvol proposes a swap).
- `minutes_since_open` (s − 09:30, minutes) is added to the trigger model, which had no time-of-day input.
- **Relative detector thresholds:** scale(s, w) = tod_norm_w(s) × clip(rvol_today(s), `of_scale_floor` 0.5, `of_scale_cap` 2.0), with tod_norm_w = the window's pro-rated normal ÷ (the typical RTH minute's normal, median_m N(m), × w ÷ 60). The floor and cap apply to today's part only (owner 2026-10-17): the time-of-day part is a 20-session median and is never capped, since the open and the close run well above 2× a typical minute every day. Effective `abs_volume` = `abs_volume` × scale(s, `abs_seconds`) (F9.5 (i)); effective `imb_min_volume` = `imb_min_volume` × scale(s, `imb_window_sec`) (F9.4). Their step-19 grids are unchanged and now mean contracts at a typical minute of a typical day; still tuned once at step 19 on months 1–4. The scale is null only when `rvol_today` is (an unrebuilt feed gap, when the outage rule already blocks entries): the detector then does not fire. Ratios and counts (`imb_ratio`, `exh_decay_ratio`, `abs_refills`, `div_min_pts`) are not volumes and are not scaled. The raw absorbed volume and imbalance volume stay as inputs, with the scaled copies (÷ scale) and the scale itself alongside. Ablation group 8 removes the B1 columns, not the scaled thresholds (they define the firings).
- **Big trades** are scaled by time of day only (F9.2: per-slot threshold), never by today's volume: a busy day mostly adds orders, not bigger ones.
- **Multi-window inputs**, for each w in `of_context_windows_sec` (5 s and 60 s), at second s: `delta_ratio_w` = (buy − sell aggressor volume) ÷ volume over (s − w, s]; `vol_tod_w`; `cvd_slope_w` (F9.3 over w); `net_refill_w` = best-level refills − pulls on the defending side (F9.8 over w); `big_trade_net_w` = big trades with the trade direction − against it (count, F9.2); `aggr_no_progress_w` (F9.9 over w). A column is not built when its window equals a tuned or fixed window of the same quantity (e.g. `aggr_no_progress_60` if `abs_seconds` is frozen at 60). At most 12 columns, plus `rvol_today` and `minutes_since_open`; all in ablation group 8.
- **Gaps and restarts:** today's per-minute volume is rebuilt by the §9 Databento intraday replay. If it can't be rebuilt, `vol_tod_w` is null for windows touching the gap and `rvol_today` is null for the rest of the session (it is cumulative); the outage rule applies.
- **Early-close days:** computed as usual against the full-session normals, minute by minute (like with like up to 13:00); a thin day shows as a low `rvol_today` and the `early_close` flag tells the model why.
- **Roll report (step 11):** `rvol_today` and `vol_tod_60` by day relative to each roll day (−5 … +5), months 1–6.
- Every one of them is in the step-11 no-lookahead shift test.
- *Months 1–6 (`tod_norm_60`, the median over 120 sessions with 20 prior full sessions; owner's question 2026-10-17):* 09:30–10:00 2.72 (p10–p90 2.05–4.14; the first 5 minutes 4.07, the 09:30 minute 8.1); 12:00–13:00 0.94 (0.76–1.16); 15:30–16:00 1.02 (0.78–2.44; the last 10 minutes 2.0, the 15:59 minute 11.0). 13% of RTH minutes have a normal above 2× a typical minute, nearly all 09:30–10:07 and after 15:50: a cap on the product would have flattened exactly those.
- *Params:* `vol_baseline_sessions`, `rvol_clip_mult`, `of_scale_floor`, `of_scale_cap`, `of_context_windows_sec`, `big_trade_slot_min`, `big_trade_slot_floor`, `big_trade_slot_cap` (features.yaml; owner, fixed, never tuned).
- *Test:* normals 3,000 for the first 30 minutes and 1,000 after: the window (09:59:45, 10:00:45] has normal 750 + 750 = 1,500; 6,000 contracts in the 09:31 minute → `vol_tod_60` 2.0; at 09:32 `tod_norm_60` 3.0, so a day at `rvol_today` 1.0 scales by 3.0 (not capped) and one at 4.0 by 6.0 (today's part capped at 2); mid-day at `rvol_today` 0.2 → 0.5; a 60,000-contract news minute against a 3,000 normal counts 15,000 in `rvol_today`; a 20-session baseline skips an early close and a damaged session; 19 sessions → null.

---

## F10. Opening range and golden zone (§5.8; the owner's setup)

**Variants (owner 2026-10-26, SPEC §13 Q-fibvariants; `features/golden_zone.py`):** the orientation flip and the 10:30 redraw are pre-declared step-13 tests, switched by `golden_zone_flip` and `golden_zone_redraw_1030` (both start true; the 2 × 2 grid `tune_grids_13.golden_zone_variants`, F16.4). `ib_fib_start_minute` (15), `ib_lock_minute` (30), the fib levels and the swing rule stay owner rules, never tuned. Everything below is the default (both on); the off behaviour is stated in the flip and redraw items.

- *Definition:*
  - **Running range** from 09:30:00: H and L = the max/min RTH trade so far on the session's front contract (F0.4); t_H and t_L = their first-occurrence times.
  - **Established** at 09:30 + `ib_fib_start_minute` (09:45:00; owner 2026-10-16, was 09:50).
  - **Locked** at 09:30 + `ib_lock_minute` (10:00:00). Trades in [09:45, 10:00) that make a new extreme redraw the fib (orientation re-evaluated); from 10:00 the fib is static, even if price leaves the range.
  - **10:30 redraw (owner 2026-10-16)** at 09:30 + `ib_redraw_minute` (10:30:00): if a trade in [10:00, 10:30) exceeded the 10:00 H or L, the fib is redrawn once on the 09:30–10:30 range (H, L, t_H, t_L of [09:30, 10:30)), orientation by the rule below; else it stays. It resets the anchors (earlier flips no longer apply) and moves the IB high/low (F3.6). The redrawn zone is a new zone (`zone_id`): an open event on the old drawing ends at 10:30:00 and its pending entries are removed (F7). No other redraw after 10:30. **With `golden_zone_redraw_1030` off:** nothing happens at 10:30; the 10:00 drawing (and the IB high/low locked at 10:00) stays all day, changed only by flips; `fib_redrawn_1030` is false from 10:30, `range_pts_1030` equals the 10:00 value and no event has `fib_drawing` = redrawn_1030.
  - **Orientation (owner):**
    - t_L < t_H (low first, then high): the leg is up, anchored low → high. Fib level x sits at H − x·(H − L) and the zone is a **long** pullback zone.
    - t_H < t_L (high first): the leg is down. Level x sits at L + x·(H − L) and the zone is a **short** pullback zone.
    - It is re-evaluated at every redraw before the lock and at the 10:30 redraw; from the lock it changes only by a flip.
  - **Orientation flip (owner 2026-10-16):** at the close of a 1-minute bar that settles past the zone's 0.79 level on the side away from the zone (the invalidation below, F0.5), the fib is redrawn the other way on the same H and L: up leg (zone at H − x·(H − L), long) ↔ down leg (zone at L + x·(H − L), short). From 09:45, all day; a new extreme before 10:00 and the 10:30 redraw set the orientation by the rule above instead. Each flip makes a new golden zone (`zone_id`), armed by F5.6 (a zone created away from price arms once price is outside its re-arm band, approach side as at that re-arm). **With `golden_zone_flip` off:** the same settled bar is only the invalidation below: it ends the setup (an open event ends, its pending entries are removed), the drawing never changes orientation and keeps its `zone_id`, `fib_flips` stays 0 and `minutes_since_flip` null, and later returns re-arm on the original side (SPEC §5.4 Containers: back on the zone side of the 0.79 edge and `rearm_dist` away).
  - **Golden zone** = between the 0.5 and 0.79 levels (a container), with fib levels 0.5, 0.618, 0.705 and 0.79 (`golden_fib_levels`; the far edge is `golden_zone_upper_fib`, 0.79 since owner 2026-10-14, was 0.786).
    - Level 0 = the range extreme in the trade direction (a target candidate).
    - Level 1.0 = the opposite extreme (stop candidate 6).
    - Extensions at −0.272 and −0.618 are target candidates.
  - **Invalidation:** a 1-minute bar settles beyond the 0.79 level, on the side away from the zone (F0.5).
  - **Re-arm:** the zone in force re-arms by the F5.6 rule (with the flip on, an invalidation flips it, so the invalidated zone does not re-arm; with it off, the invalidated zone re-arms on its original side, SPEC §5.4 Containers). Every return is a new event, and there is no limit on tests.
  - **Zone identity (owner 2026-10-26):** a new extreme before 10:00 that keeps the orientation moves the drawing and keeps its `zone_id` (a zone that moves, F5.5); one where the swing rule reverses the orientation is a **new golden zone** (new `zone_id`), like a flip: the old zone's open event ends, its pending entries are removed with `exit` (§5.5b), and its touch count and freshness don't carry over. Flips and the 10:30 redraw make new zones. A 1-minute bar judges its open against the drawing in force at its open and its close against the one at its close (F0.5; approved by the owner 2026-10-26); a zone made inside the bar (a reversal before 10:00, a flip or the 10:30 redraw) cannot be invalidated by that bar.
  - *Zone-identity tests (owner 2026-10-26):* low 20,000 at 09:31, high 20,100 at 09:40 → long; a new low of 19,950 at 09:52 → short, a new `zone_id`, the old zone's event ends; a new high of 20,150 at 09:52 instead → still long, same `zone_id`.
- *Known at:* each trade (the range); 10:00:00 (the lock); 10:30:00 (the redraw); each 1-minute bar close (a flip).
- *Outputs:*
  - `in_golden_zone`; `fib_level_reached` (the deepest of 0.5/0.618/0.705/0.79 reached by `decision_ts`; a pair column);
  - minutes after 09:30 when the range was established (15 unless redrawn), and the number of redraws before the lock;
  - `fib_drawing` ∈ {running, locked_1000, redrawn_1030} (the drawing the event's zone came from); `fib_redrawn_1030` (bool; null before 10:30:00); `range_pts_1000` and `range_pts_1030` (pts, ATR, EM; the 10:30 value null before 10:30:00, equal to the 10:00 one when no redraw);
  - `fib_orientation` ∈ {long, short}; `fib_flips` (flips of the current drawing so far) and `minutes_since_flip` (null if none), both restarting when the anchors change (a new extreme before 10:00, the 10:30 redraw; owner 2026-10-16);
  - range size (pts, ATR, EM) and the range ÷ its `ib_avg_sessions` average; `range_broke_on` (H > ON high or L < ON low);
  - touch history: today's touch number; earlier touches' outcomes and how far they ran, if resolved (pending otherwise); minutes since the last touch; minutes since the lock; `left_ib_since_lock`;
  - nodes inside:
    - which of the 4 maps have a relevant node whose band overlaps the zone (S&P maps via ES, scaled by ES ÷ NQ);
    - their count, sizes and types;
    - Trinity inside the zone;
    - confluence members (F5.9).
- *Test:*
  - L 20,000 at 09:35, H 20,200 at 09:45 → up leg, zone [20,042, 20,100], long.
  - A new high of 20,300 at 09:55 → redraw.
  - In a separate case keeping the 09:45 drawing (0.79 at 20,042): a bar at 10:05 that opens at 20,040 and closes at 20,030 → invalidated, and the fib flips: short zone [20,100, 20,158] (L + 0.5·200 to L + 0.79·200), `fib_flips` 1.
  - Flip (owner's example): L 20,000 first, H 20,100 → long zone [20,021, 20,050]; a bar settling below 20,021 → short zone [20,050, 20,079]; a later bar settling above 20,079 → long zone [20,021, 20,050] again, `fib_flips` 2.
  - 09:45 start: L 20,000 at 09:32, H 20,100 at 09:40 → established at 09:45:00 (15 minutes), a touch of 20,050 at 09:46 is a tradeable golden-zone event.
  - 10:30 redraw: 10:00 range [20,000, 20,100] (low first); a new low of 19,950 at 10:12 → at 10:30:00 redrawn on [19,950, 20,100], high first → short, `fib_redrawn_1030` true, `fib_flips` 0, the IB low moves to 19,950 (F3.6); with no new extreme in [10:00, 10:30) → unchanged, `fib_redrawn_1030` false.
  - Flip off (owner 2026-10-26): the owner's long zone [20,021, 20,050]; a bar at 10:05 settling below 20,021 → the event ends, the zone stays long [20,021, 20,050] with the same `zone_id`, `fib_flips` 0; a later bar settling above 20,079 changes nothing.
  - Redraw off (owner 2026-10-26): 10:00 range [20,000, 20,100]; a new high of 20,150 at 10:12 → nothing changes at 10:30:00 (drawing, `zone_id`, IB high 20,100 kept), `fib_redrawn_1030` false.
  - Tests: `tests/test_golden_zone.py`.

---

## F11. Definitions shared with trades and labels (§6.2–§6.4, §7.1)

### F11.1 Stop candidates (§6.2)
Every candidate sits beyond something that could support the trade. Candidates 1, 2, 3 and 6 add `stop_buffer`; candidates 2 and 3 add at least `stop_buffer_floor` of the source of the level they sit beyond (the zone member that sets the far edge: QQQ 4.0 NQ, NDXP pending, chart levels 0; owner 2026-10-08, SPEC §5.1), i.e. max(`stop_buffer`, floor); candidate 5 adds `abs_stop_buffer`, and candidate 4 (the gameplan's own stop) is used as given:
1. the **swing extreme** from the event's last re-arm to `decision_ts` (area events: over the last `swing_lookback_sec`);
2. the **far edge** of the entry zone, with the band as of `decision_ts`;
3. the **next structure** behind the entry: the far edge of the next active zone in the stop direction;
4. the gameplan's `stop`, as given (no buffer), for trades from a gameplan zone;
5. the **absorption range** extreme + `abs_stop_buffer`. It applies when absorption was detected within `abs_stop_window_sec` at or before `decision_ts`, inside the zone ± `zone_proximity` or its area. Available from step 19;
6. golden zone only: the fib 1.0 level.

Candidates within `stop_merge_ticks` of each other are merged. Area events use only candidates 1, 3 and 5.

### F11.2 Target candidates (§6.3)
- Candidates: every registry zone and confluence zone in the trade direction, each at its **near edge** and at its **centre** (bands as of `decision_ts`); the golden-zone extensions; the gameplan's T1/T2 (a range target is a band: near edge and centre; a target with no price is none, F8).
- At most `max_target_candidates` of the nearest distinct zones with net RR ≥ `pair_build_min_rr` at default costs are kept, plus the 1:2.5 baseline.
- Pair features: distance (ATR, EM), target type, confluence at the target, target-node velocity, and obstacles in between (NQ zones plus S&P path nodes, F2.20).

### F11.3 Breakeven "first structure" (§6.4)
- The first structure = the first active NQ registry zone strictly between entry and target as of `decision_ts`, excluding containers, VAH/VAL areas and ES levels. Its band is frozen at `decision_ts`.
- The trigger is the first 1-minute bar that settles past it in the trade direction. The `breakeven` signal goes out at that bar's close and takes effect `order_latency_sec` later.
- No breakeven when the target is the first structure.
- *Test:* long from 100, target 140, a zone [115, 117] between: a 1-minute bar opening at 117.25 and closing at 118 → breakeven sent at that bar's close, effective `order_latency_sec` later; the zone moving to [120, 122] after `decision_ts` changes nothing (frozen band).

### F11.4 Structure label (§7.1)
- **Held:** after the touch, price moves ≥ `hold_reaction_pts` in the hold direction from the touch's extreme (the deepest price reached into or past the zone), with a trade-through, before the zone is crossed.
- **Broke:** the zone is crossed (F5.4).
- **Timeout:** neither happens within `hold_window_min`, or all members end without a crossing.
- **Post-cross retest** (F2.6 step 5): held and broke are judged in the level's new role; broke = the zone crossed back toward its members' original side, each by its own rule.
- A sweep that reclaims counts as held only if the hold reaction follows before a cross or the end of the window.
- The resolution time (the moment the label is decided) is what F0.1 and F5.11 use.
- *Params:* `hold_reaction_pts`, `hold_window_min` (*label*).
- *Test:* ceiling zone [100, 102], `hold_reaction_pts` 10: high 103 after the touch, then a trade at 92.75 → held (93 needs a 1-tick trade-through); a settled 1-minute candle above 102 first → broke; neither within `hold_window_min` → timeout; a PDH swept to 105 and reclaimed, then a trade at 94.75 → held.

### F11.5 Trigger label (§7.1)
+`trig_fav_ticks` in the trade direction before −`trig_adv_ticks`, within `trig_window_sec`, measured from the best ask (longs) / bid (shorts) at `decision_ts` + `order_latency_sec`. *Params:* *label*.
- *Test:* a long with ask 100.00 at arrival, fav 8 / adv 6 ticks: a trade at 102.00 before any at 98.50 → positive; 98.50 first → negative; neither within the window → negative.

### F11.6 Day-type label (§7.1)
- At EOD, on RTH only (a cross = a 1-minute close on the other side): **trend** if all of these hold; otherwise **range**:
  - crosses of the 09:30 VWAP ≤ `trend_max_vwap_crosses`;
  - crosses of the developing daily POC ≤ `trend_max_poc_crosses`;
  - the close lies in the outer `trend_close_pct` of the RTH range, on the side of the new highs/lows.
- Rows: one per `daytype_snapshot_min` from 10:00, with the F3.10 inputs as of each snapshot.
- *Params:* *label*; the owner may refine them at step 6.
- *Test:* 1 VWAP cross, 2 POC crosses, close in the top 15% of the range on an up day → trend; 3 VWAP crosses → range.

---

## F12. Parameter index

Start values are in F15 (part 2); grids are in F16 (part 3). "SPEC" = the SPEC already gives a start value (used in part 2 unless the owner changes it). **NEW** = introduced here, not yet in SPEC §11 before this session. Kinds as in "How to read an entry".

| Parameter | File | Kind | Used in |
|---|---|---|---|
| `ratio_outlier_bps`, `ratio_reaccept_n`, `ratio_open_skip_sec`, `mapping_p95_max`, `atlas_match_tol`, `atlas_regression_min_share`, `ratio_window_sec`, `ratio_gap_reset_sec`, `skylit_stamp_change_date`, `board_content_offset_ms`, `disl_guard_pct`, `disl_guard_window_sec`, `disl_guard_confirm_sec`, `disl_guard_hold_sec`, `disl_guard_maps` | features (`mapping:`) | SPEC (step 8b reports, never raised to pass) | F1 |
| `skylit_max_strikes` | settings | measured (step 7) | F2.1, F2.3 |
| `node_band` | features | owner (NDXP confirmed step 13) | F1.4, F2 |
| `min_node_pct_of_king` | features | owner (0.25) | F2.4 |
| `node_drop_pct` | features | SPEC | F2.4 |
| `velocity_lookback_sec` | features | SPEC | F2.8 |
| `lean_window` | features | owner (1 min, 2026-10-05) | F2.9, F2.18 |
| `magnet_min_dist_em` | features | **NEW** | F2.10 |
| `em_level_sigmas` | features | owner rule (2026-10-14), never tuned | F3.11 |
| ~~`roll_window_min`~~ | — | removed (owner 2026-10-05: no timer on rolls) | F2.11 |
| `roll_switch_min_sec` | features | tuned-13 by structure-model log loss on months 1–4 (owner 2026-10-25, Q29; start 60) | F2.11 |
| `king_switch_min_sec` | features | tuned-13 by structure-model log loss on months 1–4, after `roll_switch_min_sec` (owner 2026-10-25, Q29; start 60) | F2.16, F2.5, F2.3 |
| `node_switch_incumbent_max_share` | features | owner rule (0.25, 2026-10-25), never tuned | F2.11, F2.16 |
| `regime_band` | features | owner (1%, 2026-10-05) | F2.12 |
| `rug_gap_strikes` | features | **NEW**, owner (1) | F2.13 |
| `pika_cloud_min_pct` | features | **NEW**, owner (0.25 since 2026-10-24, with the F2.4 relevance rule; was 0.10) | F2.14 |
| `pika_cloud_min_nodes` | features | **NEW**, owner (2) | F2.14 |
| `air_pocket_max_pct` | features | **NEW**, owner (0.10 since 2026-10-24; was 0.02) | F2.15 |
| `rainbow_min_nodes_per_sign`, `rainbow_max_dominance` | features | **NEW**, owner (2 / 1.5, 2026-10-05) | F2.17 |
| `bias_deadband` | features | **NEW** | F2.18 |
| `zone_proximity` | features | tuned-13 (§7.1) | F2.19, F5.6, F9.1 |
| `es_zone_proximity` | features | derived (`zone_proximity` × ES ÷ NQ at the open) | F2.19, F5.16 |
| `tempest_lag_sessions` | features | measured (step 7) | F2.21, F0.8 |
| `rv_lookback_sessions`, `atr_period` | features | SPEC | F2.21, F0.8 |
| `profile_update_sec` | features | SPEC | F3.2–F3.4 |
| `lvn_smooth_ticks`, `lvn_max_pct`, `lvn_merge_pts` | features | SPEC | F3.4 |
| `tpo_tick_pts`, `sp_min_ticks` | features | SPEC | F3.5 |
| `retest_tol_ticks`, `retest_lookback_sessions` | features | SPEC | F3.8 |
| `sweep_max_depth`, `sweep_reclaim_bars` | features | tuned-13 | F3.9, F6.2 |
| `ib_fib_start_minute`, `ib_lock_minute`, `ib_redraw_minute` | features | owner (15 / 30 / 60; 2026-10-16), never tuned | F10, F3.6 |
| `golden_zone_flip`, `golden_zone_redraw_1030` | features | step-13 variants (owner 2026-10-26, Q-fibvariants; start true / true): total net R per month on golden-zone trades, months 1–4 (F16.4) | F10, F3.6 |
| `seq_window_sec` | features | owner (30, fixed; 2026-10-16) | F9.9 |
| `vol_baseline_sessions`, `rvol_clip_mult`, `of_scale_floor`, `of_scale_cap`, `of_context_windows_sec` | features | owner (20, 5, 0.5, 2.0, [5, 60], fixed; 2026-10-17) | F9.10 |
| `big_trade_slot_min`, `big_trade_slot_floor`, `big_trade_slot_cap` | features | owner (30, 0.5, 2.0, fixed; 2026-10-17) | F9.2 |
| `ib_avg_sessions`, `rotation_min_pts` | features | SPEC | F3.10, F10 |
| `dp_top_n`, `dp_lookback_days`, `dp_band`, `dp_lag_sessions` | features | SPEC / owner step 13 / measured | F4 |
| `confluence_tol`, `max_zone_width` | features | tuned-13 / SPEC | F5.3 |
| `rearm_dist`, `entry_reprice_tol` | features | SPEC (one global value each) | F2.6, F5.6, F7 |
| `flip_margin` (per type), `flip_bar_minutes` | features | tuned-13 / owner | F2.6, F5.2 |
| `mx_bucket_days`, `mx_snapshot_delay_sec`, `mx_wall_reach_em` | features | owner (2026-10-24), not tuned | F2.23 |
| `chart_level_half_width`, `chart_level_half_width_cap` (per type), `react_pts`, `react_minutes`, `react_width_q` | features | frozen at step 13 / label | F5.14 |
| `area_rearm_sec`, `trig_min_gap_sec` | features | SPEC | F5.8, F9.1 |
| `smt_lookback_min`, `rel_delta_sec` | features | **NEW** | F5.16 |
| ES equivalents of NQ-point parameters | (derived, no key) | NQ value × ES ÷ NQ at the open (owner, FQ-21) | F0.11 |
| `big_trade_agg_ms`, `big_trade_min_contracts`, `big_trade_lookback_sessions` | features | SPEC / measured | F9.2 |
| `cvd_slope_sec` | features | **NEW** | F9.3 |
| `imb_ratio` | features | tuned-19 | F9.4 |
| `imb_window_sec`, `imb_min_volume`, `imb_stack_min` | features | **NEW**, tuned-19 | F9.4 |
| `abs_volume`, `abs_ticks`, `abs_seconds`, `abs_refills`, `abs_push_ticks` | features | tuned-19 | F9.5 |
| `exh_lookback_sec`, `exh_decay_ratio` | features | tuned-19 | F9.6 |
| `div_lookback_sec`, `div_min_pts` | features | tuned-19 | F9.7 |
| `tob_window_sec` | features | **NEW** | F9.8 |
| `abs_stop_buffer`, `abs_stop_window_sec`, `stop_buffer`, `stop_buffer_floor`, `entry_ttl_sec`, `entry_order_type` | features (frozen) / settings (start) | exec | F7, F11.1 |
| `stop_merge_ticks`, `min_stop_pts`, `max_stop_pts` | features | SPEC (null, owner) | F11.1 |
| `swing_lookback_sec`, `max_target_candidates`, `pair_build_min_rr`, `style_tie_ev_r` | settings | SPEC | F11.1–F11.2, F6.4 |
| `max_entry_slip_ticks`, `exit_slippage_ticks`, `order_latency_sec`, `map_latency_sec` | settings | SPEC / measured | F0.1, F7 |
| `hold_reaction_pts`, `hold_window_min` | features | label | F11.4 |
| `trig_fav_ticks`, `trig_adv_ticks`, `trig_window_sec` | features | label | F11.5 |
| `daytype_snapshot_min`, `daytype_max_features`, `trend_*` | features | label / SPEC | F11.6 |
| `daytype_inputs` | features | **NEW** (part 2), owner-approved 2026-10-05 | F3.10, F11.6 |
| `gameplan_min_days` | settings | SPEC | F8 |
| `gameplan_ny_am_end` | settings | **NEW** (owner 2026-10-07: scenario set by time) | F8 |
| `blackout_before_min`, `blackout_after_min`, `news_flatten_lead_sec`, `preopen_start` | settings | owner / SPEC | F0.1, F2.22, F7 |

**Removed:** `floor_search` (the Skylit rug has no floor-search clause), `roll_min_shrink_pct` and `roll_min_shift_strikes` (owner's roll rule, F2.11), `pika_cloud_max_gap_strikes` (owner: adjacent strikes), `abs_push_window_sec` (owner: no time limit), `roll_window_min` (owner 2026-10-05: no timer on rolls), `rug_max_gap_strikes` and `king_change_min_sec` (renamed `rug_gap_strikes` and `node_switch_min_sec`), `node_switch_min_sec` (split into `roll_switch_min_sec` and `king_switch_min_sec`, owner 2026-10-25, Q29).

---

## F13. Open questions for the owner (all answered at step 6, 2026-10-05)

**Answers 2026-10-05 (SPEC §13 Q5–Q18):** FQ-01 (a) simple mean; FQ-08 value area (a) one tick at a time, POC tie (a) nearest the range midpoint, then nearest the volume median (owner follow-up 2026-10-05), then the lower price; FQ-09 (a) bands on both anchors; FQ-10 (b) Gaussian; FQ-11 (b) one period; FQ-12 (a) both variants; FQ-13 (a) registry highs/lows; FQ-14 (b) first full bar after the break; FQ-20 (a) time-weighted; FQ-22 (c) whichever comes first, later sessions only; FQ-23 (a) crossed doesn't count; FQ-05 leftover: intraday only. All are written into the definitions above. The options below are kept as the record.

Each question lists the options as they were offered; the answers are in the line above (SPEC §13 Step 6, Q5–Q18).

**FQ-01: ATR averaging (F0.8).** §5.4 says "`atr_period`-session RTH daily ATR" without naming the average. Options:
- (a) simple mean of the true ranges;
- (b) Wilder's smoothing (the usual chart ATR, e.g. Deepcharts).

**FQ-02, FQ-03: node types and the Gatekeeper.** *Answered 2026-10-05:* Gatekeepers and significant nodes are ≥ 25% of the King; every such node between price and the King is a Gatekeeper (F2.5, FQ-24).

**FQ-04: Rug / reverse rug.** *Answered 2026-10-05:* the negative node is at most 1 strike below (rug) / above (reverse rug) the positive node, any size; the positive node must be relevant (F2.13, FQ-25); `floor_search` is dropped.

**FQ-05: Rolling.** *Answered 2026-10-05:* the ceiling is the highest-magnitude node above price; a roll-down is when a closer node (still above price) becomes the largest, whether the old one faded, the new one grew, or both (F2.11). Not answered: also measuring the roll across sessions (yesterday's last board vs today's first). Kept intraday only unless the owner asks.

**FQ-06: Pika cloud.** *Answered 2026-10-05:* adjacent positive strikes, each ≥ 10% of the King, at least 2 of them (F2.14, FQ-26). **Changed by the owner 2026-10-24: each strike a relevant node (enters at 0.25, stays until below `node_drop_pct` 0.20).**

**FQ-07: Air pockets.** *Answered 2026-10-05:* runs of strikes below 2% of the King (replacing the absolute 5k), quicker through negative ones (F2.15). **Changed by the owner 2026-10-24: below 10% of the King.**

**FQ-08: Value area and POC (F3.2–F3.3).** These should match Deepcharts (checked at step 12). Value-area expansion options:
- (a) one tick at a time, adding the side with the larger next-tick volume;
- (b) the CBOT method, comparing two-tick sums on each side.

POC ties: (a) the price closest to the profile's midpoint; (b) the price closest to the previous POC; (c) the lower price.

**FQ-09: Bands on the 09:30 VWAP (F3.1).** The registry says "18:00 and 09:30 anchors, 1σ/2σ bands". Options:
- (a) both anchors have 1σ/2σ bands (6 lines each);
- (b) bands on the 18:00 anchor only; the 09:30 VWAP is a single line.

**FQ-10: LVN smoothing kernel (F3.4).** "Smoothing `lvn_smooth_ticks`" doesn't name a kernel. Options:
- (a) a centred moving average over `lvn_smooth_ticks` ticks;
- (b) a Gaussian with σ = `lvn_smooth_ticks` ÷ 2.

**FQ-11: Single-print runs across periods (F3.5).** Can one run join single-print bins from different 30-minute periods? Options:
- (a) yes: any consecutive bins each printed in exactly one period;
- (b) no: the run's bins must all come from the same period (the classic single print of one fast move).

**FQ-12: Whipsaw, the Nasdaq side (F2.18).** The doc uses QQQ; the SPEC's Nasdaq side is QQQ **or** NDXP. Options:
- (a) record both variants as separate features (current draft);
- (b) the Nasdaq side's bias is NDXP's (the owner's edge);
- (c) the Nasdaq side counts as up or down only if QQQ and NDXP agree, else pinned.

**FQ-13: Retested-extreme candidates (F3.8).** Which earlier extremes can become retested extremes? Options:
- (a) the session highs/lows already in the registry (ON, PDH/PDL, prior RTH, prior week, IB high/low) from the last `retest_lookback_sessions` sessions;
- (b) (a) plus intraday swing highs/lows (zigzag pivots, F3.10).

**FQ-14: Counting the sweep window (F3.9, F6.2).** "Within `sweep_reclaim_bars` 1-minute bars": does the bar in which price first trades beyond count? Options:
- (a) it is bar 1;
- (b) bar 1 is the first full bar after it.

**FQ-15: "ES reacting there".** *Answered 2026-10-05:* dropped (F5.16).

**FQ-16: Relative delta.** *Answered 2026-10-05:* kept; both forms are inputs and the model learns their relevance (F5.16).

**FQ-17: Footprint imbalance.** *Answered 2026-10-05:* buyer or seller dominance; the order-flow model learns what matters (F9.4). The window, ratio and stack size are tuned at step 19.

**FQ-18: Absorption push-away.** *Answered 2026-10-05:* no time limit; absorption, then price moving away (F9.5).

**FQ-19: Exhaustion thin volume.** *Answered 2026-10-05:* one of many tested inputs, not a condition (F9.6).

**FQ-20: How velocity lean is averaged (F2.9).** §5.2 says "averaged over the last `lean_window` minutes". Options:
- (a) time-weighted average of the lean between boards (current draft);
- (b) plain mean over the boards applied in the window.

**FQ-21: ES equivalents.** *Answered 2026-10-05:* convert at the day's ES/NQ price ratio (F0.11).

**FQ-22: Retested-extreme lifetime (F3.8).** Not in SPEC. Questions:
- When does it end? (a) `retest_lookback_sessions` sessions after E's own session; (b) only when crossed and not flipped back; (c) both, whichever comes first.
- Does a retest in E's own session count? (a) yes; (b) only retests in later sessions.

**FQ-24, FQ-25, FQ-26.** *Answered 2026-10-05:* every relevant node between price and the King is a Gatekeeper (F2.5); the rug's negative node has no size rule, the positive node must be relevant (F2.13); 2 adjacent strikes make a pika cloud (F2.14).

**FQ-27: Absorption reference point.** *Answered 2026-10-05:* order-flow signs count anywhere in the zone once price is in it; the zone is where a reaction is expected and the order flow is the reaction. Absorption is measured from the visit's running extreme, wherever it is in the zone (F9.5).
**FQ-23: Crossed S&P nodes in Trinity (F2.19).** Does a SPY/SPX (or QQQ/NDXP) node that has been crossed but not yet flipped or reclaimed still count as aligned in its old role? Options:
- (a) no: a crossed node doesn't count until it is reclaimed (or counts in its new role once flipped);
- (b) yes: it counts in its old role until it flips.

---

## F14. Conflicts checked

**Owner notes vs SPEC: none found.** Checked:
- 25% of King (owner notes = §5.2);
- "QQQ level with SPY and/or SPX also approaching on ES" (= the Trinity count features, §5.2);
- golden-zone orientation, levels and settled-past-0.79 invalidation (= §5.8);
- chart levels break on a settled 1-minute candle, nodes on trading past the band, sweeps their own case (= §5.4, §5.3);
- absorption, push-away, absorbed + exhausted, big trades input only (= §5.7);
- the absorption stop (~5 pts) (= §6.2);
- the breakeven first-structure rule (= §6.4);
- setup windows (= §1.2);
- the news rule (= §9);
- Deepcharts settings (= §5.3).

**Skylit docs vs the SPEC §5.2 placeholders** (§5.2 says the docs replace them, so these are not conflicts):

| Item | SPEC placeholder | Skylit docs | Resolution |
|---|---|---|---|
| Rug | positive above negative "with no significant floor within `floor_search` below" | positive node directly above negative node(s), spot below the positive node; no floor clause | docs + owner (2026-10-05: negative node 1 strike below); `floor_search` removed |
| Rolling | the ceiling shrinks ≥ x% while the strongest ceiling moves ≥ n strikes lower (owner 2026-10-05: the largest-magnitude node above price is replaced by a lower one that grew; no thresholds) | the old ceiling fades **and** a new one appears at a lower strike; upside targets compress | same shape; target compression added as a feature; which node is "the ceiling" → FQ-05 |
| Gamma regime | net gamma within ±band ÷ King | yellow vs purple predominance | both kept: `regime_net` and `regime_pos_share` |
| Pika | listed as a node type | yellow (positive) node; pika cloud = a cluster of them | pika/barney = colour (F2.2); types King / Gatekeeper / significant / normal by size (owner 2026-10-05, F2.5) |
| Hedge nodes | "definition from `docs/skylit/`; dropped if not defined" | not defined | dropped |
| Beach ball | not in SPEC before 2026-10-04 | overshoot → reaction → reversion | mapped onto crossed → reclaimed (F2.7); owner-approved 2026-10-04 |

**Owner-approved deviations from SPEC wording made at this review:** none. Where an earlier draft drifted from SPEC (zone crossed = every *current* member, the LVN peak bound = the next *LVN*, approach side = the side at the last re-arm, absorption refills counted at event level), this file now follows the SPEC.

**For part 3 (done, F16.8):** §7 rule 5 fixes the ablation groups before step 15. The new map-shape features (F2.7, F2.10, F2.15–F2.18) get a 7th group of their own ("map shape"; owner-approved 2026-10-05, §13 Q30).

---

## F15. Start values (step 5 part 2; approved by the owner 2026-10-05, now in `config/`)

**How the list was built.** A script extracted every backticked token in `docs/SPEC.md` and this file and checked it against `config/features.yaml`, `config/settings.yaml` and `config/accounts.yaml`. Each parameter that is `null` or has no key was then checked in its SPEC context. The parameters with no start value yet are below, plus the day-type model's inputs (named in §12 step 5) and `news_title_map` (§9: "proposed at step 5"). The reasons assume NQ ≈ 30,600, a daily ATR of about 400 pts, an expected move (EM) of about 250 pts and a QQQ node band about 40 NQ pts wide (±20). Kinds are as in "How to read an entry". Tuned and frozen values here are only starting points; F16 gives their grids. The owner approved this table at step 6 (2026-10-05); the values are in `config/`.

| # | Parameter | Start value | Reason (one line) | File (§11) | Kind |
|---|---|---|---|---|---|
| 1 | `zone_proximity` | 5 NQ pts | a small buffer (~1/8 of a QQQ node band) so a touch starts just before the band | features | tuned-13 |
| 2 | `confluence_tol` | 10 NQ pts | merges thin chart levels whose bands don't overlap; = `rearm_dist`, well under `max_zone_width` (60) | features | tuned-13 |
| 3 | `chart_level_half_width_cap` | 12 NQ pts per line type (VWAP lines and bands, POC, LVN, ON / PDH-PDL / prior RTH / prior week / IB high-low / retested extremes, expected-move lines); single prints 6 | two capped levels merged at `confluence_tol` span 10 + 2 × 12 = 34 pts, well inside `max_zone_width` (60); single-print bands already span the run | features | cap |
| 4 | `chart_level_half_width` | 4 NQ pts per line type; single prints 0 (SPEC §5.3: start 0, the unfilled run is the band) | a narrow start for steps 11–12 only; the step-13 reaction report replaces it (frozen on months 1–4) | features | frozen at step 13 |
| 5 | `lean_window` | **1 min** (owner 2026-10-05; proposed 5) | owner: "accumulation shifts quickly" | features | owner |
| 6 | `regime_band` | **1** (% of spot; owner 2026-10-05; proposed 2) | ≈ ±1.2 EM, the nodes near enough to matter today; well inside `skylit_max_strikes` | features | owner |
| 7 | ~~`roll_window_min`~~ | removed (owner 2026-10-05) | a roll stays active until it is rolled back, no timer (F2.11) | — | — |
| 8 | `magnet_min_dist_em` | 0.1 EM | ≈ 25 pts, a bit more than a QQQ half-band (20 pts): a node at price pulls at most 10 × its pct_of_king | features | fixed |
| 9 | `roll_switch_min_sec`, `king_switch_min_sec` (was `node_switch_min_sec`, split by the owner 2026-10-25, Q29) | 60 s each | = `velocity_lookback_sec`; the lead-time window that keeps a near-tie from flickering (F2.11, F2.16); with `node_switch_incumbent_max_share` 0.25 (owner rule) | features | tuned-13 (log loss, F16.4) |
| 10 | `rainbow_min_nodes_per_sign` | 2 | the doc's "multiple prominent positive and negative nodes" | features | owner (2026-10-05) |
| 11 | `rainbow_max_dominance` | 1.5 | a King under 1.5 × the second node = "no dominant node" | features | owner (2026-10-05) |
| 12 | `bias_deadband` | 0.05 (lean units) | ignores a net weighted node change under 5 % per lookback; checked against the lean distribution at step 12 | features | fixed |
| 13 | `smt_lookback_min` | 15 min | the F5.16 example window, a common NQ/ES swing scale | features | fixed |
| 14 | `rel_delta_sec` | 60 s | one 1-minute bar of delta in each market | features | fixed |
| 15 | `abs_volume` | 300 contracts | a clear cluster of aggressive volume at the edge in 30 s (NQ RTH trades ~1,000+ contracts a minute across all prices) | features | tuned-19 |
| 16 | `abs_ticks` | 8 ticks (2 pts) | absorbed volume sits close to the price where buying (selling) stalls; that price can be anywhere in the zone (FQ-27) | features | tuned-19 |
| 17 | `abs_seconds` | 30 s | long enough to see aggression fail, short enough to be one push | features | tuned-19 |
| 18 | `abs_refills` | 3 | repeated refills, not one, show a resting order reloading | features | tuned-19 |
| 19 | `abs_push_ticks` | 4 ticks (1 pt) | the owner's "price starts to push away"; the grid includes 0 | features | tuned-19 |
| 20 | `imb_ratio` | 3.0 | the standard 300 % diagonal footprint imbalance | features | tuned-19 |
| 21 | `imb_min_volume` | 20 contracts | keeps thin 3-vs-1 prints from counting | features | tuned-19 |
| 22 | `imb_stack_min` | 3 | the classic stacked imbalance; the grid includes 1 (FQ-17) | features | tuned-19 |
| 23 | `imb_window_sec` | 60 s | one 1-minute footprint bar, as the owner reads it | features | tuned-19 |
| 24 | `cvd_slope_sec` | 30 s | matches `abs_seconds`, the scale of one push | features | fixed |
| 25 | `tob_window_sec` | 30 s | same scale as the absorption window | features | fixed |
| 26 | `exh_lookback_sec` | 15 s | aggression fading into the extreme over the last few seconds | features | tuned-19 |
| 27 | `exh_decay_ratio` | 0.5 | volume at least halves into the new extreme | features | tuned-19 |
| 28 | `div_lookback_sec` | 60 s | one 1-minute bar for the new high vs CVD | features | tuned-19 |
| 29 | `div_min_pts` | 2 NQ pts | a real new extreme (8 ticks), not a 1-tick poke | features | tuned-19 |
| 30 | `hold_reaction_pts` | 20 NQ pts | ≈ 5 % of ATR: a real bounce off the zone, not noise | features | label |
| 31 | `hold_window_min` | 30 min | a level reaction shows within half an hour; later moves aren't caused by the touch | features | label |
| 32 | `react_pts` | 10 NQ pts | half the hold reaction: enough to call an approach a reaction | features | label |
| 33 | `react_minutes` | 5 min | reactions to chart levels are fast | features | label |
| 34 | `react_width_q` | 0.8 | the band covers 80 % of reacted penetrations without stretching to outliers | features | label |
| 35 | `trig_fav_ticks` | 20 ticks (5 pts) | a trigger that worked moves the trade 5 pts within `trig_window_sec` (120 s) | features | label |
| 36 | `trig_adv_ticks` | 12 ticks (3 pts) | inside the owner's ~5-pt absorption stop, so a "bad" trigger is one that goes against at once | features | label |
| 37 | `entry_ttl_sec` | {a: 5, b: 5, c: 60, d: 60} | marketable limits fill at once (only a remainder waits); stop-limit and passive limit get one 1-minute bar | settings (start; frozen copy in features) | exec |
| 38 | `stop_buffer` | 5 NQ pts | no owner value ("let the backtest decide"); borrows his ~5-pt absorption buffer as a neutral start; a stop beyond a QQQ or NDXP level uses at least `stop_buffer_floor` (QQQ 4.0 NQ, NDXP pending; owner 2026-10-08) | settings (start; frozen copy in features) | exec |
| 39 | `be_offset_ticks` | 1 tick | covers the 1-tick stop slippage, so a breakeven exit nets ≈ 0 after costs; re-checked once step 13 measures `exit_slippage_ticks` (it should be ≥ that) | settings | fixed |
| 40 | `guard_port` | 8731 | an unprivileged localhost port, away from common defaults (5000, 8000, 8080, 8501) | settings | fixed |
| 41 | `engine_port` | 8732 | as above | settings | fixed |
| 42 | `ui_port` | 8733 | as above | settings | fixed |
| 43 | `ntp_server` | `time.cloudflare.com` | anycast, answers from any network; step 22 confirms it on each | settings | fixed |
| 44 | `recency_halflife_months` | 6 | the oldest of ~9 training months weighs ~0.35: drift-aware without dropping data; grid in F16.6 | settings | fold |
| 45 | `daytype_inputs` (≤ `daytype_max_features` = 8) | `minutes_since_open`, `ib_width_ratio`, `vwap_crosses`, `vwap_side_share`, `new_extreme_skew`, `range_vs_em`, `regime_net` (NDXP), `rv_iv_ratio` | the SPEC's day-type inputs (IB width, VWAP crosses, one-sidedness, new highs/lows (owner 2026-10-05: "great for telling if we're trending"), range vs EM, regime, vol); NDXP regime because the owner says NDXP has the edge; time of day because the counts grow with it (not in the SPEC lists, so the owner confirms it). Left out: `poc_crosses` and `rotations` (both overlap `vwap_crosses`: all three count chop), the rest of the vol context (for the later LightGBM day-type model) | features | owner |
| 46 | `news_title_map` | unchanged from step 4a (13 blackout entries, 9 non-blocking lines; see `settings.yaml`) | the owner's high-impact list, matched by ForexFactory title; §9 says it is proposed at step 5 | settings | owner |

**Not given a start value here (they already have a value or a rule, or another step sets them):**
- `es_zone_proximity` and every ES-side NQ-point parameter: derived (NQ value × ES ÷ NQ at the open, F0.11).
- `big_trade_min_contracts`: measured (99th percentile over `big_trade_lookback_sessions`; per 30-minute slot, clipped to 0.5–2× the session value, owner 2026-10-17, F9.2).
- `map_latency_sec`, `skylit_max_strikes` (step 7) and `tradesyncer_copy_lag_sec` (step 21 test 9 (h)): measured.
- `min_stop_pts` / `max_stop_pts`: null by the owner (no stop guardrails, §6.2).
- `paths.root`, `months`: set at step 4b. `max_workers`: null = the §11 rule.
- `gates.gate_pass_rate_min`: the owner's choice (report-only until set).
- `be_trigger`: a menu of ≤ 4 rules chosen per fold, not one value; see F16.2 (approved 2026-10-05; the owner's first-structure rule is on it).
- `threshold_grid`, `style_set_grid`, the ≤ 4 breakeven rules, the detector, execution, `recency_halflife_months` and LightGBM grids, and the ablation groups: F16.
- `warmup_sessions`, `entry_slip_ticks`, `risk_per_*`: derived by formula (§3.2, §6.1). `early_close_et`: `config/calendar.yaml`.
- Per-account fields (`lambda_risk_penalty` default 0, `last_life_top_pct` default `auto`, `lives_mode`, `funded_buffer_usd`, `payout_*`, `bot_daily_loss_cap_usd`, `max_losses_per_day`, …): the owner enters them on the Accounts page (§6.7 presets).
- Skylit API arguments (`top_n`, `lookback_days`, `min_notional`): request fields, not config.
- Removed names (F12): `floor_search`, `roll_window_min` (owner 2026-10-05), `roll_min_shrink_pct`, `roll_min_shift_strikes`, `pika_cloud_max_gap_strikes`, `abs_push_window_sec`, `rug_max_gap_strikes`, `king_change_min_sec`.

**Count:** 45 parameters found without a value; 44 get a start value here (`be_trigger` is a part-3 menu). With the day-type inputs (`daytype_inputs`, a new key) and `news_title_map`: 46 rows, 46 filled. Row 7 (`roll_window_min`) was since removed by the owner (2026-10-05, Q31), leaving 45 values.

**Noted for the owner at step 6 (approved 2026-10-05):** the day-type inputs leave out `poc_crosses`, `rotations` and most of the vol context, and add `minutes_since_open`, which the SPEC lists don't name (row 45); single prints start at half-width 0 as SPEC §5.3 says.

---

## F16. Pre-declared grids (step 5 part 3; approved by the owner 2026-10-05, now in `config/`)

**What this is.** Every per-fold or frozen choice made from outcomes picks from a menu fixed in advance (§6.5b, §7 rule 2, §7.1). This section proposes each menu, its size and what it costs to run on the laptop. The owner approved them at step 6 (SPEC §13 "Step 6", questions Q19–Q33, 2026-10-05) before any result was seen. They are in `settings.yaml` (`threshold_grid`, `be_trigger`, `style_set_grid`, `recency_halflife_months`, `lgbm_grid`, `ablation_groups`) and `features.yaml` (`tune_grids_13`, `tune_grids_19`).

**Search method for detector and execution grids (§7.1 Tuning detector parameters).**
- Every grid is searched in **one coordinate pass**, in the order listed. Each parameter is varied over its grid while the parameters before it stay at their winners and the ones after it at their start values. The winner is kept and the pass moves on.
- Every grid contains its F15 or SPEC start value, so the current best point is never re-run: a pass costs 1 + Σ(nᵢ − 1) runs.
- This is not a full factorial search. It keeps the number of settings tried (printed with the result, §6.5b) small enough that the months 1–4 score still means something.
- Detector values are scored as §7.1 and §12 step 13 say: day-blocked cross-validated total net R per month on months 1–4, with the bootstrap policy, run through the sequential rule of §7 rule 3b.

**Run-time assumptions.** These are rough. Step 13 re-measures them from the `nqbot status` 90th-percentile chunk times, and every estimate is re-printed before a job starts.
- One night ≈ 9 hours (22:30–07:30).
- §11: a full 12-month feature, event and label rebuild ≈ 1 night.
- One step-13 grid point rebuilds months 1–4 events with only the bootstrap pair per style, reuses the cached map, level and node-type timelines (§11) and cross-validates the provisional structure model: ≈ 1 hour, so ≈ 8 points per night.
- Order-flow points (step 19) also stream `mbp-1`: ≈ 1.5 hours, ≈ 6 per night.
- Execution parameters are re-simulated from the stored paths (§6.5) without a rebuild: minutes per point.
- One pair-scorer LightGBM fit on up to 8 months of pair rows in low-memory mode: ≈ 10–20 minutes.

### F16.1 Execution threshold grid (`threshold_grid`, settings; §6.5b, §6.8)

| | Values |
|---|---|
| `min_ev_r` | 0.05, 0.10, 0.20, 0.30, 0.45 |
| `min_target_prob` | 0.30, 0.40, 0.45, 0.50, 0.60 |

- **Size:** 5 × 5 = 25 (the §6.5b maximum).
- **Why these values:**
  - The grid contains `tuning_start` (0.20 / 0.40).
  - The low corner (0.05 / 0.30) leaves the Aggressive profile room below Normal (§6.8).
  - 0.60 is what a 1:1 trade needs for EV 0.2.
  - The `max_loss_run_rate` limit (L-trade runs of full losses, L = the lives the account's risk fraction allows; SPEC §6.5b, owner 2026-10-14) removes loose points that lose too often in a row. The per-account sweep runs at the account's own fraction (1/2, 1/3 or 1/4); preset entries are written at 1/3 only, and the prop-simulator risk fraction report sweeps each of the three.
  - Conservative, Protect and the last life use EV quantiles (`profile_ev_quantiles`, `last_life_grid`), not this grid.
- **Cost:** re-simulation of calibrated selections only, minutes per fold; a per-account `nqbot run sweep` takes well under 1 night.

### F16.2 Breakeven rules (`be_trigger`, settings; §6.4, §6.6)

| # | Rule | Definition |
|---|---|---|
| 1 | `none` | no breakeven |
| 2 | `first_structure` | the owner's rule (F11.3): a 1-minute bar settles past the first structure between entry and target; none when the target is the first structure |
| 3 | `r_multiple: 1.0` | the first trade ≥ 1.0 × the trade's stop distance (from the fill price) in favour |
| 4 | `r_multiple: 0.5` | the same at 0.5 × the stop distance |

- The `breakeven` signal is sent at the trigger: a bar close for rule 2, the trade's `ts_event` for rules 3–4. It takes effect `order_latency_sec` later and moves the stop to entry ± `be_offset_ticks` (§6.4).
- **Size:** 4 (the §6.4 maximum). No per-regime split (each split would count as extra rules, §6.4).
- **R-based, not points:** stop sizes differ widely, so a fixed +X points would mean something different on every trade.
- **Cost:**
  - The 4-outcome labels for all 4 rules come from one re-simulation of the stored paths: ≈ 0.5 night, once.
  - Each rule other than `none` that some fold (or fold 10, or the production fit) picks needs its own 4-outcome scorers (k = 5…9 and later): ≈ 5–6 fits at the cached grid point ≈ 1–2 hours, shared by every fold that picks it.
  - Worst case (all 3 non-`none` rules picked somewhere): ≈ 3–6 hours, under one night.

### F16.3 Entry-style sets (`style_set_grid`, settings; §7 rule 2, §7.1)

| # | Set | Note |
|---|---|---|
| 1 | {a, b, c, d} | all four |
| 2 | {a, b, c} | no plain entry |
| 3 | {a, b} | order flow only |
| 4 | {c, d} | structure only |
| 5 | {c} | audit / sweep reclaim only |
| 6 | {d} | plain only (the baseline) |

- **Size:** 6 (the §7 rule 2 maximum).
- Phase D (steps 13–16) offers only the sets whose styles exist (4–6). Sets 4–6 are also exactly the choices of the structure-only and fallback system (§12 step 20).
- **Cost:** a selection filter on cached scorer outputs, minutes per fold.

### F16.4 Step-13 grids (structure detectors and execution, months 1–4)

**Detector parameters (`tune_grids_13`, features), in search order (start in bold):**

| Order | Parameter | Grid | Scored on | New runs |
|---|---|---|---|---|
| 1 | `zone_proximity` (NQ pts) | 2, **5**, 8, 12 | all events (styles c + d) | 3 (+1 for the start point) |
| 2 | `confluence_tol` (NQ pts) | 5, **10**, 15, 20 | all events | 3 |
| 3 | `sweep_max_depth` (NQ pts) | 10, **15**, 25, 40 | all events | 3 |
| 4 | `sweep_reclaim_bars` | 1, 2, **3**, 5 | style (c) trades only (§7.1) | 3 |
| 5 | `flip_margin` (NQ pts), 3 groups: GEX nodes · chart lines (VWAP lines and bands, POC, LVNs, single prints, dark pools) · highs/lows (incl. IB and retested extremes) | **0**, 2, 5 per group | all events | 6 |

- **Size:** 19 rebuilds ≈ **2.5 nights** (SPEC §11: 1–3).

**Feature-only parameters (`tune_grids_13.features_logloss`, owner 2026-10-25, Q29):** `roll_switch_min_sec` and `king_switch_min_sec`, grid {30, **60**, 120, 300} s each (on 1-minute-resolution days a value under 60 behaves as 60). They change features only (events and structure labels are identical), so they are scored by day-blocked cross-validated **log loss** of the structure model on months 1–4, on the fixed event set (§7.1 Tuning: log loss compares settings on fixed events and labels); events are not rebuilt. `roll_switch_min_sec` first (King at 60), then `king_switch_min_sec` (rolls at the winner). Pre-declared rule: a value replaces 60 only if its log loss is lower than 60's and the 95% day-block bootstrap interval of the difference excludes 0; otherwise 60 stays. The report lists each grid value's log loss and its difference vs 60 with the interval. Written to `features.yaml` with `frozen_from: months 1-4`; months 7–12 are never used. Cost: 6 feature rebuilds of months 1–4 from the cached boards plus a provisional structure-model CV each, well under a night.
**Golden-zone variants (`tune_grids_13.golden_zone_variants`, owner 2026-10-26, SPEC §13 Q-fibvariants):** `golden_zone_flip` {**on**, off} × `golden_zone_redraw_1030` {**on**, off}, 4 variants. For each, the golden-zone events are rebuilt on months 1–4 (F10's off behaviour) and scored like the other structure parameters (§7.1 Tuning: day-blocked cross-validated total net R per month with the bootstrap policy), on golden-zone trades only (the zone's events, styles (c) + (d), through the sequential rule); the report gives each variant's trade count. Pre-declared rule: the current design (on, on) stays unless a variant has a higher total net R per month with the 95% day-block bootstrap interval of the difference excluding 0; if several do, the one with the largest lower bound. Written to `features.yaml` with `frozen_from: months 1-4`; months 7–12 are never used. Searched after the detector parameters, at their winners, before the execution parameters. Cost: 3 golden-zone-only rebuilds of months 1–4, well under a night.
- `flip_margin` is per level type (SPEC §11): 14 NQ types (the keys of `flip_margin` in `features.yaml`, mapped from the registry sub-types in F5.14). Three groups instead of 14 types keep the run count down (owner-approved, Q23); a group's winner is written for each of its types. ES-side types take their NQ counterpart's value × ES ÷ NQ (F0.11): SPY/SPX nodes from GEX nodes, SPY dark pools from chart lines, ES ON high/low and PDH/PDL from highs/lows.
- `max_zone_width` (60) and `rearm_dist` (10) keep their SPEC values (one global value each, F12).
- The chart-level half-widths and the NDXP band (±10 / 15 / 20) come from the reaction-distance report (F5.14), not from a grid; the owner confirms the NDXP band and `dp_band` there.

**Execution parameters (`tune_grids_13`), re-simulated from the stored paths.**
- The order type and time-to-live are searched per style ((c) and (d), each on its own single-style policy) as a **full joint grid**: they interact (a passive limit needs a longer life than a marketable one), and re-simulations are cheap.
- `stop_buffer` is one config value for every style (§6.2), so it is searched once afterwards, on the pooled (c)+(d) trades.
- Styles (a)/(b) inherit `stop_buffer` at step 19; their own stop (candidate 5) uses `abs_stop_buffer`.


| Order | Parameter | Grid |
|---|---|---|
| 1 (joint, per style) | `entry_order_type` × `entry_ttl_sec` | {limit, marketable_limit, stop_limit} × {10, 30, **60**, 120, 300} s (start types: (c) **stop_limit**, (d) **limit**) |
| 2 (pooled c + d) | `stop_buffer` (NQ pts) | 1, 2.5, **5**, 8, 12 (none dropped: `stop_buffer_floor` applies per stop by level source, F7) |

- **Size:** 2 styles × 15 + 4 = 34 re-simulations ≈ **≤ 1 night**.
- **Step 13 in total:** ≈ 3–4 nights, including the final full-window rebuild with the winners (≈ 1 night).

### F16.5 Step-19 grids (order-flow detectors and execution, months 1–4)

**Style (a) absorption (`tune_grids_19`), scored on style (a) trades:**

| Order | Parameter | Grid |
|---|---|---|
| 1 | `abs_volume` (contracts) | 150, **300**, 600 |
| 2 | `abs_ticks` | 4, **8**, 12 |
| 3 | `abs_seconds` | 15, **30**, 60 |
| 4 | `abs_refills` | 1, **3**, 5 |
| 5 | `abs_push_ticks` | 0, **4**, 8 (includes 0, §5.7) |

Size: 1 + 5 × 2 = 11 runs.

**Style (b), scored on style (b) trades:**

| Order | Parameter | Grid |
|---|---|---|
| 1 | `imb_ratio` | 2.5, **3.0**, 4.0 |
| 2 | `imb_window_sec` | 30, **60**, 120 |
| 3 | `imb_min_volume` | 10, **20**, 40 |
| 4 | `imb_stack_min` | 1, 2, **3**, 4 (includes 1, FQ-17) |
| 5 | `exh_lookback_sec` | 10, **15**, 30 |
| 6 | `exh_decay_ratio` | 0.35, **0.5**, 0.7 |
| 7 | `div_lookback_sec` | 30, **60**, 120 |
| 8 | `div_min_pts` | 1, **2**, 4 |

Size: 1 + 7 × 2 + 3 = 18 runs.

Both passes start from the same start-value rebuild (every detector is computed in one rebuild), so that run is shared.

- **Detectors in total:** 11 + 18 − 1 shared start = 28 runs ≈ **4–6 nights**.
- `big_trade_*`, `cvd_slope_sec`, `tob_window_sec` and `trig_min_gap_sec` are inputs or firing spacing, not trigger conditions, so they stay at their F15 / SPEC values.

**Execution (`tune_grids_19`), re-simulated from the stored paths.** The order type × time-to-live grids are full joint grids, as at step 13; the two absorption-stop parameters are a coordinate pass:

| Order | Parameter | Grid | Scored on |
|---|---|---|---|
| 1 | (a) `entry_order_type` × `entry_ttl_sec` | {**marketable_limit**, stop_limit} × {2, **5**, 10, 30} s | style (a) trades (its passive limit is report-only, §5.5b) |
| 2 | (b) `entry_order_type` × `entry_ttl_sec` | {**marketable_limit**, stop_limit, limit} × {2, **5**, 10, 30} s | style (b) trades |
| 3 | `abs_stop_buffer` (NQ pts) | 2, 3.5, **5**, 8 | every style's trades with stop candidate 5 (§7.1) |
| 4 | `abs_stop_window_sec` | 60, **120**, 300 | as above |

- **Size:** 8 + 12 + (1 + 3 + 2 − 1 shared start) = 25 re-simulations ≈ **≤ 1 night**.
- **Step 19 in total:** ≈ 3–6 weeks of nights, mostly the ablation arms (F16.8, F16.10).

### F16.6 Recency half-life (`recency_halflife_months`, settings; §7 rule 2, §7.2)

- **Grid:** 3, **6**, 12 months, and `none` (every month weighs the same). **Size:** 4.
- **How it is chosen:** searched once per pair scorer (k = 5…9, then the production fit; it weights only the pair scorer's rows, §7 rule 2) by forward validation: train on months 1…k−2, score month k−1 by log-loss.
- **Order:** this runs after the LightGBM grid point is chosen; the LightGBM search itself uses the start half-life (6).
- **Reuse:** the grid point and half-life chosen for scorer k serve its 3-outcome and 4-outcome versions and every relabel; they are searched again only when features change (§11).
- **Cost:** 4 fits × 5 scorers = 20 fits ≈ 0.5 night, cached.

### F16.7 LightGBM grid (`lgbm_grid`, settings, NEW key; §7.1)

| | Values |
|---|---|
| `num_leaves` | 7, 15, 31 |
| `min_data_in_leaf` (unweighted rows; LightGBM's own count) | 100, 500 |
| fixed | `learning_rate` 0.03 with early stopping (≤ 2,000 rounds; stops after 50 without improvement on the held-out day-block fold of the 3-fold CV; the final fit uses the mean best round count of the 3 folds); `feature_fraction` 0.7; `bagging_fraction` 0.8 with `bagging_freq` 1; `lambda_l2` 10; `max_bin` 63; `deterministic: true`; fixed seed |

- **Size:** 3 × 2 = 6 points, chosen by 3-fold day-blocked cross-validated log-loss inside the scorer's own training months (§7 rule 2).
- **Other models:** the structure and trigger models use the same grid, searched once per training set. The day-type model is a regularised logistic regression with no grid (§7.1).
- **Cost:**
  - Pair scorer: 6 points × 3 folds × 5 scorers = 90 fits ≈ **3 nights**. Lower models (fewer rows): ≈ 0.5 night.
  - Everything is cached and reused by later folds and ablation arms, and searched again only when features change.
  - With F16.6 this is most of the first walk-forward's 4–8 nights (§11): step 15 in total ≈ 4–6 nights.

### F16.8 Ablation groups (`ablation_groups`, settings, NEW key; §7 rule 5)

| # | Group | What is removed |
|---|---|---|
| 1 | Trigger model | the pair scorer's trigger-probability inputs (own firing, max and latest); styles (a)/(b) still fire on their detectors |
| 2 | Trinity (cross-market) | every feature read from ES, SPY or SPX: F2.19 Trinity, the F2.20 S&P path features, F5.16 (`es_at_level`, SMT, relative delta, NQ vs ES relative strength), the S&P maps in the golden-zone node features, and for area events `es_at_level` and every `area_sp_*` column (owner 2026-10-26, Q-areasp; cutting group 5 removes the area events themselves). S&P path nodes still count as obstacles in target selection (F11.2). |
| 3 | Confluence features | the F5.13 zone makeup beyond the zone's primary member: `stack_count`, per-type counts, member spread and per-type distances from the centre, container makeup, confluence at target. Kept: the primary member's type, node size, node type and freshness; zone width; distances |
| 4 | Day-type input | P(trend) in the structure model and pair scorer, and `yesterday_day_type` |
| 5 | Area events | VAH/VAL area events and their pairs (the VAH/VAL context features stay) |
| 6 | Stop candidate 5 | the absorption stop |
| 7 | **Map shape (owner-approved 2026-10-05, Q30)** | the six concepts approved 2026-10-04: beach ball (F2.7), magnets (F2.10), air pockets (F2.15), King dominance and migration (F2.16), rainbow road (F2.17), whipsaw (F2.18) |
| 8 | **Order-flow extras (owner 2026-10-16)** | the trigger-model additions: the F9.9 sequence inputs (B2) and the 5-minute range (B3); the F9.10 multi-window order flow, `vol_tod_w`, `rvol_today` (in every model that has it), the scaled volumes and the scale, and the trigger model's `minutes_since_open` (B1, owner 2026-10-17); the scaled detector thresholds stay |
| 9 | **Approach (owner 2026-10-16)** | the F5.18 approach features (B4) |
| 10 | **Post-cross retests (owner 2026-10-23)** | the post-cross retest events (F2.6 step 5) and their pairs, and the F2.6b cross features; what remains is the 5-minute rule alone |
| 11 | **AMT previous-session profile (owner 2026-10-24)** | the F3.2b levels (`prev_poc`, `prev_vah`, `prev_val`) with their events and pairs, and the F3.12 context features (`poc_migration_em` included); `sp_sessions_stacked` (F3.5) |
| 12 | **Multi-expiration context (owner 2026-10-24)** | the F2.23 features |

- **Why a 7th group:** these concepts are new, untested and many (≈ 40 columns). Folding them into "confluence features" would make that cut mean two different things. Rug, pika, rolling and regime stay in the base system (§7.1 lists them as structure-model inputs).
- **Size:** 12 groups (SPEC §7 rule 5 updated 2026-10-05; groups 8 and 9 added by the owner 2026-10-16, group 10 on 2026-10-23, groups 11 and 12 on 2026-10-24, each about +1–2 nights per round).
- **Cost:**
  - Each arm runs the full per-fold pipeline for folds 7–9 with cached grid points: ≈ 1–2 nights.
  - Round 1 = 7 arms ≈ 7–14 nights. Each cut adds a round over the remaining groups.
  - Group 7 adds about one arm per round (≈ +1–2 nights per round).

### F16.9 Not tuned, and no regime splits

- **Fixed, not tuned-13 (owner 2026-10-05, Q31):** `lean_window` 1 min, `regime_band` 1%, `rainbow_min_nodes_per_sign` 2, `rainbow_max_dominance` 1.5. `roll_window_min` is removed: a roll stays active until rolled back (F2.11). F12 had them as "part 3 decides"; my reasons for not tuning them are below.
  - They only shape features. Each grid value would need a full feature rebuild plus a model fit, and LightGBM can use them well at any sensible value.
  - Leaving them fixed also keeps the number of settings tried small.
- **No regime splits** are pre-declared for breakeven (§6.4), entry order types (§5.5b) or chart-level zone widths (§5.4b). Regime breakdowns stay report-only.

### F16.10 Totals and the SPEC / config changes made

| Step | Grids run | Estimate |
|---|---|---|
| 13 | F16.4 (19 rebuilds + 34 re-sims), then the final full rebuild | ≈ 3–4 nights |
| 15 | F16.1–F16.3, F16.6, F16.7 per fold | ≈ 4–6 nights |
| 19 | F16.5: 28 rebuilds (≈ 5) + 25 re-sims; full rebuild (≈ 1); walk-forward (1–2); F16.8 round 1: 7 arms × (1–2) = 7–14; structure-only and logistic arms (2–4) | ≈ 16–26 nights before any cut; each cut adds a round of ≈ 6 × (1–2) nights; ≈ 3–6 weeks of nights in all |

SPEC and config changes (all applied 2026-10-05 after the owner's step-6 approval, CLAUDE.md rule 12):
- *Done 2026-10-05 (Q30 approved):* §7 rule 5 and §11 Expected run time: six → seven ablation groups (round 1 = seven ablation arms, plus structure-only and the logistic baseline once). Step 19 "≈ 2–4 weeks" → "≈ 3–6 weeks" of nights (arithmetic above; the 2–4 weeks didn't count the later elimination rounds).
- *Done:* §11 Where settings live: the new keys `lgbm_grid` and `ablation_groups` (settings.yaml), and `tune_grids_13` and `tune_grids_19` (features.yaml).
- *Done:* §6.4: the breakeven candidates "(none, +X pts, +1R, first structure)" become "(none, +0.5R, +1R, first structure)". §6.4 and §11 name the key `be_trigger` (the one menu of ≤ 4 rules, as in settings.yaml) instead of `be_trigger_*`.
- *Done:* §7.1 Tuning detector parameters: the entry-trigger parameters scored on their own style's trades also include `imb_*` (style b).
- *Done 2026-10-05 (Q31 answered):* the F16.9 parameters are owner-fixed and `roll_window_min` is removed (F2.11, F12, F15, SPEC §5.2, §11, `features.yaml`).
