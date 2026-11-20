# Pre-13d: the sequence after 13c (owner's review first)

Branch `pre-13d` (local only, not pushed, not merged), on top of `main` at a76e2ad:

| commit | item | build key | features_version |
|---|---|---|---|
| 90390f9 | 1. `stack_level_ids` recorded (Q-review-stack (b)); `nqbot step13 compare-builds` | changes (code) | 39 |
| 33d22e1 | 2. retested-extreme age from the print session (Q-review-stack (c), F3.8) | changes | 40 |
| 0b1db4b | 3. `nqbot status` time left and finish date (Q-status-eta) | unchanged (display only) | 40 |
| (this doc) | the sequence below | - | - |

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
git merge --ff-only pre-13d     # main has not moved since the branch: a fast-forward keeps the four commits
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
13d build refuses run-time overrides). Then:

```
uv run nqbot test               # full suite on the new HEAD: PYTEST EXIT CODE: 0
git push                        # owner's OK
```

## 7. 13d: the full-window build with the winners

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_worktree.ps1 -Commit <HEAD from step 6>
cd C:\nqb\build
uv run nqbot features plan --months 1-12         # committed configuration only; features_version 40; key K13d
uv run nqbot queue add feature_build             # the resume task runs it around the clock (nothing else queued)
uv run nqbot status                              # pass-by-pass time left and the finish date (item 3)
```

One build alone (slot 1); `parallel_builds` may stay on. Rough size: b958fe6e42aa took 5 h 24 min for 84 sessions
with two builds at once part of the time, so 251 sessions alone is on the order of 15 h; `nqbot status` measures it.

## 8. Open before 13d's labels and count reports (code not written; the owner decides)

- `nqbot step13 counts` reads **every** built session of the slot-1 plan (`step13b.counts` → `built_days`): on the
  1-12 build it would put months 7-12 into `counts.md` (§7 rule 2b). It needs a months 1-6 limit before it runs on
  K13d (the months 1-6 build counts of Q-step13plan (2), (9)).
- `nqbot step13 labels-plan` and `step13_labels` label months 1-4 only (`sessions_14`); the labels for 13d's months
  1-6 (the owner's review round, Q-review-sample) and for the full window (step 15) need the session set widened.
- The months 5-6 reaction rows (step 13a (5), Q-maprole) come from the 13d build too.
- After the merge, `git branch -d pre-13d` (owner's OK).
