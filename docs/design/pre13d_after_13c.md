# Pre-13d: the sequence after 13c (owner's review first)

Branch `pre-13d` (local only, not pushed, not merged), on top of `main` at a76e2ad:

| commit | item | build key | features_version |
|---|---|---|---|
| 90390f9 | 1. `stack_level_ids` recorded (Q-review-stack (b)); `nqbot step13 compare-builds` | changes (code) | 39 |
| 33d22e1 | 2. retested-extreme age from the print session (Q-review-stack (c), F3.8) | changes | 40 |
| 0b1db4b | 3. `nqbot status` time left and finish date (Q-status-eta) | unchanged (display only) | 40 |
| 7fb8fc4 | this doc (first version), Status line | - | - |
| 0263b2c | owner 2026-11-20: a carry without the weekly print days stops with `CarryTooOld` (Q-review-stack (c)) | changes | 40 |
| 9ccee87 | owner 2026-11-20: `step13 counts` and `labels-plan` on months 1-6, never 7-12; the build report shows months 7-12 as progress only (Q-pre13d-months) | changes (a docstring in `checks/feature_build.py`) | 40 |
| a221a4a | this doc (second version), SPEC, feature_spec F3.8, Status | - | - |
| 6a3a956 | owner 2026-11-20: the re-score command (job `step13_rescore`, section 8); `step13 reactions-plan` / `reactions-report --months 5-6` (the months 5-6 node rows, section 7b) | unchanged (outside both keys) | 40 |
| c2047f9 | this doc (third version): sections 7b, 8, 9, 10; SPEC, Status | - | - |
| a972e3c | owner 2026-11-20: the stop floor is a setting (`stop_floor_vol` in settings.yaml, read by the labeler; section 6) | unchanged (outside the build key; the labels key's code changes) | 40 |
| 6c07dd5 | this doc (fourth version), SPEC, Status | - | - |
| (next) | owner 2026-11-21, step 6: the 13c winners into features.yaml / settings.yaml (`zone_proximity` 5 kept, `sweep_max_depth` 10, `sweep_reclaim_bars` 2, `entry_ttl_sec` c 120, `entry_order_type` d marketable_limit, `stop_buffer` 5 and `stop_floor_vol` null kept; SPEC §13 Q-step13c (e)); SPEC, Status | changes (features.yaml) | 40 |

Item 1 is its own commit so the comparison builds **exactly** item 1 on the configuration the 13c baseline
b958fe6e42aa was built with (no build code changed between the 13c pin b6f7d1d and a76e2ad: only
`config/accounts.yaml` / `presets.yaml`, outside the build key). So the comparison runs **before** the 13c winners are
written into the configuration.

Nothing below starts until 13c has ended: `uv run nqbot status` shows `step13_tune: 61/61 chunks done (complete)`
and no job lock held, and `uv run nqbot queue show` has no job running.

## 1. Full suite on main, push (owner's OK for the push)

```
git switch main
git status                      # clean
uv run nqbot test               # PYTEST EXIT CODE: 0 (recorded in <data_dir>/jobs/test_passes.jsonl)
git push                        # main's local commits since the last push (UI stages 1-5, polish round 1, docs)
```

## 2. Merge the branch

```
git merge --ff-only pre-13d     # main has not moved since the branch: a fast-forward keeps its commits
```

(If main has moved by then: `git merge --no-ff pre-13d`, and step 3 still uses 90390f9.)

## 3. Full suite on the item-1 commit (the pin needs its recorded pass, rule 14 (f))

```
git switch --detach 90390f9
uv run nqbot test               # PYTEST EXIT CODE: 0 on 90390f9 (its features_version is still 39)
git switch main
```

## 4. The comparison build: ledger month 1 at 90390f9

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_worktree.ps1 -Commit 90390f9
cd C:\nqb\build
uv run nqbot features plan --months 1            # note the key it prints: K1 (not b958fe6e42aa: the code changed);
                                                 # check: features_version 39, no overrides, 23 sessions
uv run nqbot run feature_build --until 07:30     # one build alone, about 1.5 h; or: uv run nqbot queue add feature_build
```

Why month 1 is the smallest slice that proves it: the planner plans whole ledger months, and pass 1 must replay
every session from the warm-up up to the last one compared, so no build is cheaper than month 1. It covers sessions
without Skylit boards (10-01 to 10-06) and with them, level zones and golden-zone containers, retested extremes (the
own-extreme exclusion and the B2 world), and decision rows. The 13b proof (Q-step13b (f) (2)) showed a month-1 plan's
files equal the months 1-4 build's month-1 files, so b958fe6e42aa is the reference and only one new build is needed.

## 5. The comparison

```
uv run nqbot step13 compare-builds b958fe6e42aa K1 --allow-new stack_level_ids
```

Read-only, development months only; report `<reports_dir>/step13/compare_builds_b958fe6e42aa_K1.md`, exit 0 = PASS.
PASS means, on all 23 sessions: every per-session parquet file of K1 (touches, daytype, outcomes, levels, events,
decisions, decision_rows, level_events) has every b958fe6e42aa column equal, row for row (nulls and NaN equal; file
metadata, which names the key, not compared); the only added column is `stack_level_ids`, and it appears (in touches
and decision_rows); the pass-1 checkpoints `state/state_<D>.json` are equal; the day summaries are equal without their
`*_sec` timings. The labeler's folders (labels, pairs, fills, paths) are not build files and are skipped. On FAIL:
stop and show the owner the report (a day-summary field that is neither a count nor a timing would show as
"differs" there; nothing is changed to make it pass).

## 6. The 13c winners into the configuration (owner)

The owner reads `<reports_dir>/step13/tune/tune_summary.md` and approves; the winners are written into
`config/features.yaml` / `config/settings.yaml` with `frozen_from: months 1-4` (one commit with the owner's OK; the
13d build refuses run-time overrides).

Row 13's winner goes into `config/settings.yaml` through the settings alone, whichever floor wins (the labeler reads
the floor from `stop_floor_vol` since the pre-13d branch; no override is left anywhere):
- `stop_buffer:` row 13's winning buffer;
- floor **fixed**: `stop_floor_vol: null` (as committed now) and `stop_buffer_floor` unchanged;
- floor **vol**: `stop_floor_vol:` the `override` object of `<reports_dir>/step13/tune/volfloor/volfloor.json`, copied
  exactly (its `window_min` and each source's `min` and `k`, e.g. `{window_min: 15, QQQ: {min: m, k: k}}`);
  `stop_buffer_floor` stays as it is (the sources the fit doesn't name keep their fixed floor).

Either way the build key doesn't move (both settings are outside it); the labels key does, as it should. The labeler
checks the value when it reads it (a window of at least 1 minute, a `min` and `k` of at least 0 per source), so a
mistyped floor stops `labels-plan` with an error rather than labelling. To see the floor the labeler will use before
committing (`None` = fixed):
`uv run python -c "from nqbot.config import load_settings as s, load_features as f; from nqbot.labeler import params as lp; print(lp.params(s(), f()).stop_floor_vol)"`.
Then:

```
uv run nqbot test               # full suite on the new HEAD: PYTEST EXIT CODE: 0
git push                        # owner's OK
```

## 7. 13d: the full-window build with the winners

What 13d builds (SPEC §12 step 13): "**13d** the full-window build with the winners and the months 1–6 count reports",
and "the level jumps by type, the sessions with a zone difference from the candidate-time fix and the shares at 30
beside 20 come from the 13d full-window build; there is no extra months 5–6 build" (also §13 Q-step13plan (2): "Months
1–6 build counts ... wait for the 13d build; no extra months 5–6 build"). So the build covers months 1-12 (251
sessions), and everything shown from it is months 1-6 only:

| 13d output | months it reads or shows |
|---|---|
| `step13 counts` → `counts.md` | 1-6 (`sessions_dev`, each day checked before a file opens; 9ccee87) |
| `step13 labels-plan` + `run step13_labels` → `labels_<key>.md` | 1-6 (9ccee87; the labeler refuses any other day) |
| `review round-new` (the owner's 50-100-card round) | 1-6 (every card read checks its day) |
| item (8) below: the execution re-score | 1-4 (13c's tuning months) |
| `<reports_dir>/step11/feature_build.md` | dates and values 1-6; months 7-12 as one progress line (sessions, passes done), no events or rows (9ccee87) |
| `nqbot status` | chunk and session counts over the window, no dates |
| the months 5-6 node reaction rows (Q-maprole) → `reactions_nodes_months_5-6.md` | 5-6 (section 7b; months 7-12 refused; 6a3a956) |

Nothing from months 7-12 is shown. If a months 7-12 session fails, the owner is told that a walk-forward or sealed
session failed and its error type, not its date or values. The log under `<root>/logs` has the date; it isn't quoted.

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_worktree.ps1 -Commit <HEAD from step 6>
cd C:\nqb\build
uv run nqbot features plan --months 1-12         # committed configuration only; features_version 40; key K13d
uv run nqbot queue add feature_build             # the resume task runs it around the clock (nothing else queued)
uv run nqbot status                              # pass-by-pass time left and the finish date (item 3)
```

One build alone (slot 1); `parallel_builds` may stay on. Rough size: b958fe6e42aa took 5 h 24 min for 84 sessions,
with two builds at once for part of the time, so 251 sessions alone is on the order of 15 h; `nqbot status` measures
it. K13d's months 1-4 files equal a months 1-4 build of the same configuration (no lookahead; the 13b proof, Q-step13b
(f) (2)), so items (8) and (9) need no extra build.

## 7b. 13d's months 1-6 reports, once the build is complete

From `C:\nqb\build` (pinned at step 6's HEAD, so every job here runs that commit's recorded full-suite pass), after
`uv run nqbot status` shows `feature_build: ... (complete)`. Each job holds both data locks, so they run one after
another (queue them in this order, or run each by hand):

```
uv run nqbot step13 counts                                  # counts.md, months 1-6 (seconds, no lock)
uv run nqbot step13 reactions-plan --months 5-6             # the months 5-6 node reaction rows (Q-step13a-2 (5), Q-maprole)
uv run nqbot run step13a_reactions --until 07:30            # 41 sessions, one process each; 13a's 84 took ~0.2 h
uv run nqbot step13 labels-plan --firings-ok                # the labels of months 1-6 (the owner's review round)
uv run nqbot run step13_labels --until 07:30                # 125 sessions, ~25 s each (~50 min)
```

The node rows end in `<reports_dir>/step13/reactions_nodes_months_5-6.md` (with `.json` and `.png` beside): the same
F5.14 measurement as `reactions.md`, for QQQ / NDXP / SPY / SPX nodes on months 5-6 of K13d. They are a report only:
no band is frozen from them. The months 1-4 widths report (`reactions.md`, `proposed_widths.json`) is never rewritten
by it. To compare, the months 1-4 rows stay in `reactions.md`, which was measured on 13a's build at 13a's widths.

## 8. Re-score 13c's execution winners against their runners-up on the corrected build (months 1-4, no rebuild)

Why: rows 12 and 13 were scored before the age fix, on events from build bb10b8f5585e (the detector winners). K13d is
the corrected build with every winner, so its months 1-4 are the events the execution settings will trade. Only the
winner and its runner-up per decision are compared, with the same method: fixed events, the provisional model's bar
per fold, single-style policies, and the excess net R per month with its 95% day-block interval (Q-step13c (5), (7)).
Nothing is re-tuned.

| row | decision | winner | runner-up | 13c lead [95%] |
|---|---|---|---|---|
| r12 (c) | order type x `entry_ttl_sec` | stop_limit, 120 | stop_limit, 300 | +0.05 [-5.10, +5.21] |
| r12 (d) | order type x `entry_ttl_sec` | marketable_limit, 60 | marketable_limit, 10 | +0.00 [-1.96, +2.05] |
| r13 | `stop_buffer` x floor | from r13's report (running) | from r13's report | from r13's report |

Labels needed on K13d, months 1-4 (`labels_plan_for`, about 11 min a set):
- the committed winners: one set;
- r12's runners-up: one set (`entry_ttl_sec.c` 300 with `entry_ttl_sec.d` 10). Each style is scored on its own pairs,
  so one labeler run serves both;
- r13's runner-up: one set, at its `stop_buffer` and floor.

The scores then take a few minutes.

Built (6a3a956), outside both keys; after 7b, from `C:\nqb\build`:

```
uv run nqbot step13 rescore-plan --build-key K13d     # prints each label set and its overrides; needs r12 and r13 in winners.json
uv run nqbot run step13_rescore --until 07:30         # chunk labels:K13d (labels-day children, 4 at once), then score:K13d
```

Report: `<reports_dir>/step13/tune/rescore_exec_K13d.md` (and `.json`). For each comparison (r12 (c), r12 (d), r13
buffer, r13 floor) it shows:
- both points' net R per month and trades;
- the runner-up's excess over the winner with its 95% interval, and a verdict (runner-up better / winner better /
  no clear difference);
- the fs rule's figure, and 13c's own lead for reference.

What it does:
- It reads `winners.json` and row 13's report, never writes them, and never writes the configuration.
- It uses its own cache (`<data_dir>/jobs/step13_rescore/cache`), so 13c's cache isn't touched.
- Each label set is labelled once: equal points share a set, and the committed winners' set is the build's own
  settings.
- The committed floor is read from the settings (`stop_floor_vol`, section 6). A point on the vol floor carries the
  fit's `override` (`<reports_dir>/step13/tune/volfloor/volfloor.json`), a point on the fixed floor carries `null`;
  either is an override only where it differs from the committed floor. With step 6 done, the winners' set is the
  committed configuration itself.

The owner decides what a reversed sign means, e.g. keep the 13c winner unless the runner-up is better with its
interval above 0. A change would be one config commit and moves no build, because the execution settings are outside
the build key.

## 9. The zone-detection rows that were close calls, and the age fix

A close call here means the winner's and the runner-up's excess intervals overlap: the lead's 95% interval contains 0.
That holds for every detector and variant row except r08. What it takes to change a winner depends on the row's rule:
- a best-value row (r01-r05) changes if the point estimates swap order;
- a keep-current row (r06-r11) changes only if a challenger's whole interval moves above 0.

What the age fix changes, from the read-only first-order estimate on b958fe6e42aa, months 1-4 (SPEC §13
Q-review-stack (c)):
- It only shortens retested extremes' lives. There are no same-day retests, because a day's new retests come from the
  carried candidates only.
- 142 of 2,265 retested-extreme level-days go (6.3%).
- At most 1,284 of 22,121 events (5.8%, an upper bound) had an affected retested extreme in the zone at the touch.
- 43 events (0.2%) go; the rest keep the touch with one member fewer.
- Retested extremes are in 46.9% of the events (21.1% as the primary member), so the reach is wide but shallow.

The same events change in every value's build, so a row's difference moves only through how the fix interacts with
that row's parameter.

| row | rule | winner | runner-up: lead [95%] | how the fix touches the row | could the winner plausibly change? |
|---|---|---|---|---|---|
| r01 `zone_proximity` | best | 8 | 12: +3.89 [-13.45, +20.52] | all zones, the same affected events at every value | unlikely |
| r02 `confluence_tol` | best | 10 (start) | 5: +3.38 [-4.54, +11.50] | fewer retested members to merge, at every tolerance | unlikely |
| r03 `sweep_max_depth` | best | 25 | 10: +7.05 [-4.40, +18.30] | sweeps at highs/lows, retested extremes among them | unlikely |
| r04 `sweep_reclaim_bars` | best ((c) only) | 2 | 5: +3.52 [-4.10, +11.23] | style (c) fires at sweep reclaims, retested extremes among them | unlikely, can't rule out |
| r05a `flip_margin` GEX nodes | best | 0 (start) | 2: +4.59 [-3.90, +13.32] | none beyond shared zones | very unlikely |
| r05b `flip_margin` chart lines | best | 0 (start) | 2: +1.64 [-7.93, +11.51] | none beyond shared zones; the smallest best-value lead | unlikely |
| r05c `flip_margin` highs/lows | best | 0 (start) | 2: +5.52 [-3.65, +15.25] | direct: `retested_extreme` is in the group | unlikely, can't rule out |
| r06 golden-zone variants | keep | (True, True) | (False, True): -1.97 [-11.02, +6.28] | golden-zone subset; the challenger needs about +6.3 | very unlikely |
| r07 `va_edge_sweep` | keep | False | True: +6.59 [-2.97, +16.58] | VA-edge subset; the challenger needs about +16.6 | very unlikely |
| r08 `prev_profile_anchor` | keep | globex | rth: +9.85 [+0.61, +19.92] | not a close call | no |
| r09 `node_relevance_top_n` | keep | None | 5: +4.29 [-8.08, +14.84] | node subset; the challenger needs about +14.8 | very unlikely |
| r10 `node_role_at_formation` | keep | approach | strike_side: +6.66 [-2.73, +15.44] | node subset; the challenger needs about +15.4 | very unlikely |
| r11 `retest_lookback_sessions` | keep | 10 (start) | 5: +0.17 [-11.45, +12.31] | direct: the fix shortens every PDH/PDL/prior-RTH lookback by one session and prior-week ones by more, so 3 and 5 lose a larger share of their lives than 10 | the point estimates can swap easily (a tie now), but the winner changes only if 5's lower bound rises above 0 (about +12.3): unlikely |

Leads are the winner minus the runner-up in excess R/month. For a keep-current row, "needs" is how far the best
challenger's lower bound must rise.

Reading: the fix touches r11, r05c and r04 directly, and none of them is near its rule's threshold.

**The no-rebuild estimate (run 2026-11-20, owner's OK; `<reports_dir>/step13/age_fix_estimate.md`):** read-only, on
each value's stored 13c build, months 1-4, with 13c's cached predictions and picks (nothing fitted or relabelled; 4 min
on one core while 13c labelled r13's sets). First, 13c's comparison was rerun, and it reproduced every reported excess
(the script stops on any mismatch). Then every filled trade whose zone at its decision held a retested extreme the fix
removes that day was marked (the Q-review-stack (c) first-order method, each build at its own
`retest_lookback_sessions`). Finally the row was rescored with those trades dropped from every value's run and
re-picked by its own rule. Dropping is an upper bound: the fix mostly keeps the touch with one member fewer.

| row | trades the fix touches (by value) | winner (13c) → with them dropped | lead over the runner-up: 13c → dropped |
|---|---|---|---|
| r04 | 3: 29 of 247; 1: 25 of 253; 2: 31 of 242; 5: 25 of 239 (10-13%) | 2 → 2 | +3.52 [-4.10, +11.23] over 5 → +2.48 [-5.36, +10.26] over 1 |
| r05c | 0: 43 of 592; 2: 41 of 608; 5: 42 of 594 (7%) | 0 → 0 | +5.52 [-3.65, +15.25] → +3.71 [-5.29, +13.31] over 2 |
| r11 | 10: 45 of 625; 3: 37 of 651; 5: 36 of 604 (6-7%) | 10 → 10 | +0.17 [-11.45, +12.31] → +0.28 [-11.12, +11.76] over 5 |

No winner changes, even with every touched trade gone, so no rebuild re-check is proposed. The touched share is close
across values within each row, as the reasoning above expected. (On the start builds 124-127 of about 2,265
retested-extreme level-days go; 85-88 with an unresolved print session are left out, as in Q-review-stack (c).)
A real re-check would rebuild a row's values on months 1-4 at `features_version` 40, about 7.6 h per pair of builds
plus labels; the owner is asked before any.

## 10. Open (the owner decides)

- Row 13's runner-ups for section 8 are read when `rescore-plan` runs (13c still running row 13 at this writing).
- Done (owner 2026-11-20): row 13's winning floor is committed through the settings either way (`stop_floor_vol`,
  section 6).
- Done on the branch: `step13 counts` and `labels-plan` on months 1-6 (9ccee87; this corrects the first version of
  this doc: `counts` had read months 1-4 only, never months 7-12, so the gap was months 5-6 missing, not months 7-12
  leaking); the re-score command and the months 5-6 node rows (6a3a956); the build report's months 7-12 progress line
  kept (owner 2026-11-20).
- After the merge, `git branch -d pre-13d` (owner's OK).
