# Pre-SSD fixes (from the SSD-day dry run, 2026-10-05)

A fresh reviewer walked through SSD day (steps 4b, 7, 8, 8b, 9, 10) against CLAUDE.md, SPEC.md, `config/` and the code, without changing anything or calling any paid API. Tests at the time: 272 passed. `status`, `run dummy` and `step7 symbols` refuse correctly while `paths.root` is unset. Issues 1 and 4 were re-checked against the code by the main session.

All 17 issues are fixed **before the SSD arrives**, in three sessions the owner starts one at a time:

| Session | Issues | Theme |
|---|---|---|
| **A** ✅ done 2026-10-05 | 1, 2, 7, 9, 10 | step-10 backfill job, step-7 order and report, 365-day reach (independently reviewed twice; 311 tests pass) |
| **B** ✅ done 2026-10-05 | 3, 4, 5, 6, 8, 11 | separate locks, Databento retries and cost guard, `nqbot setup-ssd`, warm-up, keep-awake and checks (independently reviewed twice; 363 tests pass) |
| **C** ✅ done 2026-10-06 | 12, 13, 15, 16, 17 | step-8b gate code, rclone backup command, the small ones (independently reviewed twice) |
| – | 14 | resolved by owner decision 4 |

Each session: read this file, CLAUDE.md, and the SPEC sections listed for its steps; fix, test, get an independent review, update SPEC/CLAUDE.md, tick the issues off here, commit and push.

## Owner decisions (2026-10-05)

1. **Step 10 starts as soon as the strike window and sample days check out.** Parity, the stream test, the late-print mornings and the Tempest mornings run alongside and don't hold it up (as SPEC §12 step 7 already says).
2. **Buy a credit pack on SSD day so the whole backfill runs straight through** (no waiting for the 1st-of-month reset). The step-7 `report` prints how many credits are short (backfill estimate − current balance, with a margin) and which pack (or packs) from the §3.1 price list covers it most cheaply. The backfill waits for the owner's OK (and the purchase) before it starts.
3. **Separate Skylit and Databento locks** so the step-10 and step-8 backfills run in parallel on the laptop.
4. **The 28 archived gameplans are all `.md` files.** The owner downloads the "Jeron gameplans" folder from the Google Drive website into `data/raw/gameplans/` (the website gives a .zip; extract it there, no `.gdoc` shortcuts).
5. **Google One 100 GB is bought. Back up with rclone straight from the SSD**, not Drive for desktop.
6. **The laptop stays plugged in and on overnight.** The runner asks Windows to keep it awake.

## Issues (most harmful first)

### CRITICAL

**1. The step-10 Skylit backfill isn't built (session A).** Status: ✅ fixed 2026-10-05 (session A): job `skylit_backfill`, `nqbot skylit plan`; dark pools stay one date per call (owner 2026-10-05; range calls left to issue 17).
- Evidence: `src/nqbot/jobs/registry.py` registers only `dummy`, `skylit_sample_days` and `databento_backfill` (`nqbot run skylit_backfill` → "invalid choice"). There's no backfill date plan, no dark-pool or Tempest backfill, and `step7.backfill_estimate` (`checks/step7.py` ~758) only estimates. `skylit_credits.check` (~85–87) refuses any estimate above the balance.
- Failure: on SSD day the owner OKs step 10 and there's nothing to run. Every trading day of delay loses one session of Skylit history for good.
- Fix: a resumable `skylit_backfill` job (one CME day per chunk). It pulls oldest first: RTH range windows, holiday probe, early-close days to `early_close_et` + one probe window. It also pulls the dark-pool prints (window + `dp_lookback_days`, oldest first, using the 31-day range calls where the API allows) and Tempest history (`warmup_sessions` + 2 sessions before month 1). It skips days already out of reach (issue 7) and stops at yesterday. It reuses cached sample days (issue 10). It writes the "days beyond 365" and "share of 1-minute sessions" reports. Credit gate: the whole estimate is approved once with `--approve-credits` after the pack is bought (decision 2). If the balance runs out anyway, the job stops cleanly at the chunk boundary and resumes after a top-up. It never pulls past the approved amount.

**2. The CLAUDE.md step-7 order would delay step 10 by 1–2 weeks (session A).** Status: ✅ fixed 2026-10-05 (session A): CLAUDE.md order and `report` (pending sections, shortfall, cheapest pack).
- Evidence: CLAUDE.md put `report` (parameter check + backfill estimate) after the late-print ×5 / Tempest ×2 mornings. SPEC §12 step 7 says those don't hold up step 10.
- Fix: the order is symbols → strike-window → owner OKs `skylit_max_strikes` → sample-days → historical-instants / darkpool / tempest-depth → **report → owner OK (+ pack purchase) → step 10 and step 8 the same day**. Stream-record, parity, late-prints and Tempest-poll run alongside. Session A makes `report` work before the live-session and morning checks exist (marking them "pending", not failing) and print the credit shortfall and the cheapest covering pack (decision 2).

### HIGH

**3. One SSD job lock makes everything take turns (session B).** Status: ✅ fixed 2026-10-05 (session B): `.nqbot_skylit.lock` / `.nqbot_databento.lock` (`jobs/lock.py` `job_locks`); jobs declare `locks`, any other job holds both; `nqbot unlock [--skylit|--databento] --yes` (never a live holder on this machine); `nqbot status` shows both locks and each job's peak RAM. Parallel fit: neither pull job uses workers, and `min_free_gb` reads the drive's free space, so it counts both.
- Evidence: `jobs/runner.py` ~178 (every `nqbot run` job), `cli.py` ~178–183 (step-7 checks), `cli.py` ~242 (`databento plan`).
- Failure: steps 10 and 8 can't run in parallel. While either runs, `stream-record` (RTH only), `parity` and `darkpool` are refused. If Skylit holds the lock all night, Databento's oldest included days slip into pay-as-you-go.
- Fix (decision 3): two job locks on the SSD. The Skylit lock is held by step-7 checks and Skylit jobs. The Databento lock is held by `databento plan` and Databento jobs. Every other job holds both. Both lock files record the machine, so a second machine is still refused (§11 One machine). Step-7 checks that only read the Skylit cache don't block the Databento backfill. `nqbot status` / `unlock` show both locks. Also check that the two parallel jobs' combined memory and `max_workers` fit the 16 GB laptop and that the `min_free_gb` check counts both.

**4. A Databento download that breaks mid-way stops the overnight job (session B).** Status: ✅ fixed 2026-10-05 (session B): included items retry `BentoError` (not `BentoClientError`) with the backoff, then stop cleanly (`api_error`); a broken paid item is recorded in `payg_failed.jsonl` and waits for `databento plan --retry-failed-payg`. Billing (Databento docs, "Streaming vs. batch download"): a streamed request is billed for the data sent, and a re-stream is billed again.
- Evidence: databento 0.87.0 `common/http.py` wraps any mid-stream error as `BentoError("Error streaming response: …")`. `DOWNLOAD_RETRYABLE = (OSError, BentoServerError)` (`clients/databento_pull.py:60`) doesn't catch it, and `run_job` re-raises.
- Failure: one Wi-Fi blip at 01:00 ends the night.
- Fix: for included items, retry `BentoError` (but not `BentoClientError`) with the existing backoff. For pay-as-you-go items, stop the item cleanly, record it, and carry on with the rest. Find out (Databento docs/support) whether a broken paid download is billed before any paid item is ever re-run.

**5. "`get_cost` > 0 means pay-as-you-go" might flag the whole window (session B).** Status: ✅ fixed 2026-10-05 (session B): the one-hour `ohlcv-1s` + `trades` probe and the "more than half of the included requests priced" refusal; the dead note removed.
- Evidence: `clients/databento_pull.py` ~226–233. The "included, get_cost says $X" branch in `databento_cost.py` ~76–77 can never fire.
- Failure: if `get_cost` quotes list prices for plan-included data, or the plan runs before the Standard subscription is active, every request prints as pay-as-you-go. Approving that total could bill it for real.
- Fix: `databento plan` first prices one tiny included request (e.g. one recent hour of NQ `ohlcv-1s`). If it isn't $0, it refuses with "subscription not active, or get_cost doesn't show included data as $0: check before approving". `make_plan` also refuses when most date-included items are priced above $0. Mark the dead branch or remove it.

**6. Nothing supports step 4b (session B).** Status: ✅ fixed 2026-10-05 (session B): `nqbot setup-ssd [--drive] [--root] [--rewrite-ledger] [--yes]` (`setup_ssd.py`; a first setup must name the drive; NTFS via psutil, edits only the two settings.yaml lines), strict `months.validate` on every read (all 12 months when settings.yaml loads), merge guard while neither backfill has a plan. Independent review (2026-10-05) found and we fixed: a paid chunk whose items all broke stayed "done" after `--retry-failed-payg` (its id now carries the plan), the guard could lock out `databento plan` (now the ledger is fixed once either plan exists), and a single internal second partition could be picked as the SSD. A second fresh review found and we fixed: `--root nqbot\` crashed (now `/`), a Databento plan alone fixed the ledger (now only step 10's plan does), a merged month 1 still too short (now refused), powercfg read only in English, settings.yaml line endings, an empty `locks`, the shared log without a pid; owner check added for Modern Standby.
- Evidence: the marker, `paths.root`, window and ledger are hand edits. `months.ledger` (`months.py` ~28–35) doesn't check for 12 consecutive months or `YYYY-MM` strings (an unquoted `2025-10-01` turns into a date and matches nothing). The `min_month1_sessions` merge isn't computed anywhere. The marker is only found at the drive root (`storage.py` ~54–59).
- Fix: `nqbot setup-ssd`. It finds the SSD (asking which drive if more than one is plugged in), checks it's NTFS (`fsutil fsinfo volumeinfo`) and the free space, and creates `.nqbot_drive` at the drive root and the `paths.root` folder. It computes the 12-month window from today (the last full month before the step-7 pull) and the month-1 merge from Skylit's reach. It validates and writes `paths.root` and `months` into `settings.yaml` with a round-trip YAML writer (comments kept), printing the diff and asking first. `months.ledger` validates strictly on every load. A second command, or a flag, rewrites the ledger when step 10 starts if the merge applies.

### MEDIUM

**7. Skylit's 365-day limit is a moment in time, not a date (session A).** Status: ✅ fixed 2026-10-05 (session A).
- Evidence: `docs/skylit/openapi.json` (`from`: "Up to 365 days back"). `pull_window` only tolerates a 404 (`clients/skylit_pull.py` ~139–145). `databento_pull.window_days` cuts at date level (~91–96). `backfill_estimate` excludes neither out-of-reach nor future days.
- Failure: the oldest day's morning windows expire during the day. An out-of-range answer (probably a 400) crashes the pull. Databento may buy a day whose Skylit morning is already gone.
- Fix: clip by timestamp (now − 365 days, with a safety margin parameter). Treat an out-of-reach answer as a "lost" window: logged, reported, not cached as empty, not an error. The estimate counts only reachable past windows. The Databento window follows the days Skylit actually got (or the same timestamp rule).

**8. The warm-up count is defined twice and the copies disagree (session B).** Status: ✅ fixed 2026-10-05 (session B): `nqbot/warmup.py` (21); the Tempest start is now 23 sessions before month 1.
- Evidence: `checks/step7.py` `warmup_sessions` = max(20, lookbacks) = 20 (~706–718). `clients/databento_pull.py` `warmup_sessions` = 21 (~68–70, the owner's 2026-10-05 rule).
- Fix: one shared function (max(20, lookbacks) + 1) used by both, with a test.

**9. The dark-pool depth probe could stop the whole check (session A).** Status: ✅ fixed 2026-10-05 (session A); the probe outcome is kept in `darkpool/probe/<date>.json` so it isn't paid for twice.
- Evidence: the probe date (~mid-August 2025, more than 365 days back) sorts first and is pulled first (`checks/step7.py` ~483–495). An empty answer is cached as a permanent empty trading day (~440–451).
- Fix: the probe is best-effort and runs last. A 400 or empty answer is recorded as "out of reach / no coverage" in the report, never cached as an empty day, and never stops the check.

**10. Raising `skylit_max_strikes` after the sample days would trip step 10 (session A).** Status: ✅ fixed 2026-10-05 (session A).
- Evidence: the cached sample days keep the old parameters, so `skylit_store` raises `CacheMismatch` (~226–243), also through `SkylitPullJob.chunks` → `uncached_windows`.
- Fix: the step-10 job uses the `repull/strike_window_raise` copies when they match the current parameters, or pulls those days normally if not. Never `CacheMismatch` mid-backfill. Test it.

**11. Nothing keeps the laptop awake overnight (session B).** Status: ✅ fixed 2026-10-05 (session B): `power.keep_awake()` around every `run_job`; `power_warnings()` (`powercfg /qh`, which also shows the lid action some laptops hide from `/query`; hibernate added) printed by `nqbot run` and `setup-ssd`.
- Evidence: no `SetThreadExecutionState`, and no check of power, sleep, lid or USB selective suspend.
- Fix (decision 6): `nqbot run` calls `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` while a job runs and clears it at the end (no-op off Windows). `nqbot setup-ssd` and `nqbot run` print warnings (no changes) if `powercfg` shows AC sleep on, the lid action isn't "Do nothing", or USB selective suspend is on. Owner checklist for SSD day: `powercfg /change standby-timeout-ac 0`, lid close on AC = "Do nothing", USB selective suspend off. The keep-awake call doesn't stop lid-close sleep.

**12. The step-8b mapping gate isn't built (session C).** Status: ✅ fixed 2026-10-05 (session C): `nqbot step8b precision|spot-lag|atlas|report` (`checks/step8b.py`, maths in `features/mapping.py`: F1.2 re-accept, F1.3 open skip, F2.3 % of King, F1.6 error, ±120 s spot lag, trailing 15-minute RV terciles (owner)); the Databento job pulls the step-7 sample days first (8b needs no warm-up).
- Evidence: `features/mapping.py` only rebuilds R. There's no error report against `mapping_p95_max`, no spot-lag cross-correlation (±120 s) and no Atlas side-by-side procedure.
- Fix: `nqbot step8b` (or a `checks/step8b.py`) producing the §5.1 / §12 step-8b report. The Databento plan pulls the step-7 sample days (and their warm-up) first, so 8b can run on night 1 instead of waiting for the oldest-first pull to reach month 6.

**13. There's no backup command (session C).** Status: ✅ fixed 2026-10-05 (session C): `nqbot backup` (`backup.py`, job `skylit_backup`): `raw/skylit/` only (owner) to `gdrive:NQ bot backup/raw/skylit/`, `rclone copy --checksum` + `rclone check --one-way` per folder (per month under `range/`), fingerprinted chunks, Skylit job lock.
- Evidence: SPEC §11 Storage requires two Google Drive copies with checksums. Nothing implements it.
- Fix (decision 5): `nqbot backup`, using rclone from the SSD to the owner's Google Drive remote (`rclone copy` + `rclone check`, with checksums). It checks rclone is installed and the remote is configured, writes a report, and is resumable. Owner one-time setup: install rclone, run `rclone config` to authorise Google Drive (the owner does the OAuth in the browser; the token stays outside the repo). The remote name is a setting.

**14. Gameplans saved as Google Docs would copy as `.gdoc` shortcuts.** Status: resolved by decision 4 (all `.md`, downloaded from the Drive website).

### LOW

**15. `stream-record` timing and reconnect cost (session C).** Status: ✅ fixed 2026-10-05 (session C): refuses a start whose 15 min + 60 s would pass the close, hard wall-clock end, at most 3 reconnects (`SkylitStream.max_reconnects`), incomplete recordings marked.
- Evidence: only the start time is checked, so a start near 15:50 records past the close (`checks/step7.py` ~302–305). The `_record` loop never ends if no events arrive, and keeps reconnecting at 4 credits each time.
- Fix: refuse a start that can't finish 15 minutes before 16:00 (or `early_close_et`), add a hard wall-clock end, and cap the reconnects.

**16. CLAUDE.md drift (session C).** Status: ✅ fixed 2026-10-05 (session C): unbuilt commands marked with their step; the lock sentence was already right after session B; step8b and backup commands added.
- Evidence: CLAUDE.md lists commands that don't exist yet (`ui`, `live`, `preopen`, `setup-tasks`, `guard`, `run sweep`). It says every step-7 check except `tempest-poll` holds the lock, but `report` and `sample-days` don't take it themselves.
- Fix: mark the not-yet-built commands "(step N, not built yet)" and correct the lock sentence (after session B's lock split).

**17. Smaller robustness gaps (session C).** Status: ✅ fixed 2026-10-05 (session C): `storage.replace_retry` for every rename; `OutOfCredits` is a clean `credits` stop / message; step-7 `LockHeld` exits 3; dark pools by 31-day calls once the step-7 darkpool check proves them print by print (owner 2026-10-05), else one date per call.
- Evidence and fixes:
  - `quarantine_partial` calls `os.replace` with no retry if antivirus is holding the file (`clients/skylit_store.py` ~259) → short retry loop on `PermissionError`.
  - `nqbot run skylit_sample_days` shows a raw traceback on `OutOfCredits` / `CreditRefused` (only `LockHeld` is caught, `cli.py` ~83–88) → a clean message and exit code.
  - Dark prints are pulled one date per call where 31-day range calls exist → use range calls if the Flowseeker docs confirm the same data (also used by the issue-1 job).

## Not issues (checked)

- TLS: plain `httpx` (sync and async) and `requests` work in this venv with pip-system-certs active, so the explicit TLS context in `gameplan/parser.py` isn't needed for the Skylit or Databento clients (checked against python.org, not their hosts).
- Step 7 as a whole costs about 4,000–4,700 credits, and no single check needs the 5,000-credit approval.
