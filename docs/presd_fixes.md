# Pre-SSD fixes (from the SSD-day dry run, 2026-10-05)

A fresh reviewer walked through SSD day (steps 4b, 7, 8, 8b, 9, 10) against CLAUDE.md, SPEC.md, `config/` and the code, without changing anything or calling any paid API. Tests at the time: 272 passed. `status`, `run dummy` and `step7 symbols` refuse correctly while `paths.root` is unset. Issues 1 and 4 were re-checked against the code by the main session.

All 17 issues are fixed **before the SSD arrives**, in three sessions the owner starts one at a time:

| Session | Issues | Theme |
|---|---|---|
| **A** ✅ done 2026-10-05 | 1, 2, 7, 9, 10 | step-10 backfill job, step-7 order and report, 365-day reach (independently reviewed twice; 311 tests pass) |
| **B** ✅ done 2026-10-05 | 3, 4, 5, 6, 8, 11 | separate locks, Databento retries and cost guard, `nqbot setup-ssd`, warm-up, keep-awake and checks (independently reviewed twice; 363 tests pass) |
| **C** ✅ done 2026-10-06 | 12, 13, 15, 16, 17 | step-8b gate code, rclone backup command, the small ones (independently reviewed twice) |
| – | 14 | resolved by owner decision 4 |
| **D** ✅ done 2026-10-06 | D1–D12 (dry run 2) | failing backfill windows recorded and retried each night, strike-window gate, lock-free alongside checks, `nqbot stop`, dropped-connection tries, strike-window split, SSD-day checklist (independently reviewed three times; 462 tests pass); **check D** ✅ 2026-10-06: D1 and D12 gaps fixed (472 tests pass) |

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

## Dry run 2 findings and decisions

Second SSD-day dry run, 2026-10-06. A fresh agent read CLAUDE.md, this file, the SPEC sections for steps 4b, 7, 8, 8b, 9 and 10, and the code, and walked through SSD day read-only: nothing changed, no network, no credits. 411 tests pass. Without the SSD, `nqbot status` and `nqbot setup-ssd` refuse cleanly (exit 2). The main session re-checked findings D1, D2, D4 and D5 in the code. Session D (2026-10-06) fixed all twelve with the owner's decisions below.

### SSD day as it runs today

Before starting: `SKYLIT_API_KEY`, `DATABENTO_API_KEY` and `ANTHROPIC_API_KEY` in `.env` (there is no `.env.example`); the SSD formatted as NTFS; the CLAUDE.md power checklist; rclone installed and the `gdrive` remote set up; the Databento Standard subscription before `databento plan` (the plan refuses unless a one-hour `get_cost` probe comes back at $0).

| # | Command | Writes (all under `<SSD>:\nqbot\`) | Credits / $ | Gate |
|---|---|---|---|---|
| 4b | `uv run nqbot setup-ssd --drive E:`, then the same with `--yes` | With `--yes`: `E:\.nqbot_drive`, `E:\nqbot\`, `paths.root` and `months` in the repo's `config/settings.yaml` (commit) | 0 | Owner OKs the diff. Today the window is 2025-10..2026-09; the month-1 merge applies from about 2026-10-20 |
| 7a | `step7 symbols` | `reports/step7/symbols_*.json` | free | – |
| 7b | `step7 strike-window --busy-day D --sample-days D1,…,D5` (month 6 = Mar 2026) | `raw/skylit/fullchain/` | ≈ 150 | Owner OKs `skylit_max_strikes` (a hand edit) |
| 7c | `step7 sample-days --dates D1..D5` (job `skylit_sample_days`) | `raw/skylit/range/<date>/` | ≈ 3,250 | Printed first; under 5,000 needs no OK |
| 7d | `step7 historical-instants`, `step7 darkpool --date D [--check-dates …]`, `step7 tempest-depth` | `historical/`, `darkpool/{trades,range,top_prints,probe}/`, `vol/` | ≈ 200 + 300–700 + 1–2 | – |
| 7e | `step7 report` | `reports/step7/parameter_check.md` (backfill estimate, shortfall, cheapest pack) | free | Owner OKs, buys the pack (likely about $100), approves N |
| 10 | `skylit plan --approve-credits N`, then `run skylit_backfill --until 07:30` | `data/jobs/skylit_backfill/…`, `raw/skylit/…`, `reports/step10/coverage.{md,json}` | ≈ 155–160k | Owner's N |
| 8 | `databento plan`, `databento plan --approve-usd X`, `run databento_backfill --until 07:30` | `raw/databento/…`, `processed/bars/`, `processed/rolls/`, `data/jobs/databento_backfill/` | $199/month + pay-as-you-go (warm-up trades, the oldest L1 days); the amount is printed by `get_cost` | Owner approves X |
| Alongside | `step7 stream-record` (RTH, ≈ 90), `parity --session D` (25), `late-prints-morning` ×5, `late-prints-compare`, `tempest-poll` ×2, `tempest-compare`, then `report` again | `raw/skylit/{stream,repull/…}`, `reports/step7/` | small | Owner stops if parity shows revised boards |
| 8b | After the first Databento night: `step8b precision`, `spot-lag`, `atlas --date D --minutes …`, `report` | `reports/step8b/mapping_gate.md` | free | Owner's Atlas replay check |
| 9 | Download the Drive folder into `<SSD>\nqbot\data\raw\gameplans\`, then `gameplan-batch --approve-usd X` | `data/processed/gameplans/`, `reports/gameplans/step9_report.md` | ≈ $9 worst case for 28 files | Owner approves X and confirms the file → date list |
| Backup | `uv run nqbot backup` | `gdrive:NQ bot backup/raw/skylit/`, `reports/backup/backup.md` | Drive quota | Takes the Skylit lock |

### Findings (most harmful first)

**D1. HIGH: one bad Skylit window stops the whole step-10 backfill, and each re-run pays for it again.** Status: ✅ fixed 2026-10-06 (session D): failed windows and dark-pool dates are recorded in `data/jobs/skylit_backfill/failed.json` (answer kept in `skylit/failed/`, any conversion or write failure included), the job carries on, `skylit_max_failures_in_row` (5) in a row stop it (status `failures`), each is retried at the start of every run and the end of every night, oldest first, until it leaves the reach (then lost; owner decision after session D) (refunded: one more call; paid: converted again for free), coverage.md lists the rest; only Skylit answers are skipped. `step7 oldest-window` added. The backfill prints the balance first. **Check D (2026-10-06):** dark-pool dates were not covered (a malformed print was a plain KeyError that ended the night and lost the paid pages); fixed, with Tempest and the owner's one-re-pull rule (see Check D below).
- Cause:
  - `run_chunk` (`src/nqbot/clients/skylit_backfill.py:503-517`) retries only a `SkylitError` with a transient code and re-raises anything else:
    - a 400/404 away from the 365-day edge;
    - a 404 `symbol_not_found` (`src/nqbot/clients/skylit_pull.py:173-183`);
    - a conversion failure: `pull_window` re-raises `ValueError` (`skylit_pull.py:186-188`), and a `TypeError` isn't caught at all, so that paid body isn't saved.
  - The runner re-raises it (`src/nqbot/jobs/runner.py:232-239`) and the run ends.
  - The window is never marked as failed, so every re-run pulls it again (25 credits) and fails the same way while the oldest sessions age out of reach.
  - This breaks SPEC §11: a charged answer that can't be converted is kept in `failed/` and never retried.
  - Step 7 only tests conversion on March 1-second days. The oldest days, pulled first, may be 1-minute data.
- Fix: decision 4 below. Also catch `Exception` around the conversion so the raw body is always saved, and add one 25-credit test window from the oldest day still in reach to step 7.

**D2. HIGH: nothing stops a backfill with a strike window narrower than step 7 proposed.** Status: ✅ fixed 2026-10-06 (session D): `step7.strike_window_check` (`skylit_max_strikes` ≥ the proposal, sample days pulled with it, no edge violations); `skylit plan` refuses without it (no override), the report says NOT READY.
- Cause:
  - `_strike_check` (`src/nqbot/checks/step7.py:1002-1014`) only prints "now: N; proposed by strike-window: M". It says the sample days "match" whenever they were pulled with the current value, even if that value is below the proposal.
  - `skylit plan` (`skylit_backfill.py:354-392`) doesn't check the proposal or the sample days' `edge_violations`.
  - If the settings.yaml edit is forgotten, every backfill day loses its edge strikes for good.
- Fix: decision 3 below.

**D3. MEDIUM-HIGH: the "alongside" checks can't run while the backfill does.** Status: ✅ fixed 2026-10-06 (session D): the short checks skip the Skylit lock and write only their own files (own `ledger_<check>.jsonl`, late-print lookback dates in `repull/late_print/lookback/`, copied by the backfill for free); `skylit_alongside_reserve_credits` (1,000); `nqbot stop <job>` (status `stopped`).
- Cause:
  - `stream-record`, `parity`, `late-prints-*` and `tempest-compare` take the Skylit lock (`src/nqbot/cli.py:264-266`). So do `skylit_backfill` (`skylit_backfill.py:445`) and `nqbot backup`.
  - CLAUDE.md and decision 1 say these checks never hold up step 10.
  - There is no clean stop command, and Ctrl-C during a call can throw away a paid 25-credit answer.
- Fix: decision 2 below.

**D4. MEDIUM: `step7 sample-days --raised` silently pulls nothing, and the report then says the sample days match.** Status: ✅ fixed 2026-10-06 (session D): job `skylit_sample_days_raise`, chunk ids carry the value (a second raise pulls again), the report reads the raised copies.
- Cause:
  - `plan_sample_days(raised=True)` (`src/nqbot/checks/step7.py:236-247`) writes a plan for the same job name, `skylit_sample_days`, with the same chunk ids (the dates).
  - That job's `state.json` already marks them done (`runner.py:203`), so the run reports "complete 5/5" without pulling.
  - `sample_days_report` then reads the old tree but records the raised `skylit_max_strikes`, so `_strike_check` says "matches".
  - The test passes only because it deletes `state.json` by hand (`tests/test_step7_review_fixes.py:274`).
  - No money is lost (the backfill re-pulls those days), but the raised edge check never happens.
- Fix: give the raised run its own job name (e.g. `skylit_sample_days_raise`), or reset its state when the plan is written. The report reads the `strike_window_raise` tree.

**D5. MEDIUM: an antivirus lock or another unexpected error ends the night.** Status: ✅ fixed 2026-10-06 (session D): `replace_retry` backs off from 0.1 s to 5 s for about 30 s; the Defender exclusion is on the SSD-day checklist.
- Cause:
  - `replace_retry` waits only 20 × 0.1 s (`src/nqbot/storage.py:25-26`). Windows Defender scanning a freshly written parquet of tens of MB may hold it longer (unverified).
  - Any non-Skylit exception in a chunk is re-raised by `runner.py:232-239`, so the overnight job dies. A failed window write is a lost paid window.
- Fix: retry for about 30 s with backoff. Checklist: decision 5 below.

**D6. MEDIUM: nothing guards against Windows Update restarting the laptop on backfill nights.** Status: ✅ fixed 2026-10-06 (session D): Windows Update paused for 1 week on the CLAUDE.md SSD-day checklist.
- Cause: active hours and paused updates are only in the live-trading checklist (SPEC §9).
- Fix: decision 5 below.

**D7. LOW-MEDIUM (billing unverified): a connection dropped after a 200 is retried as if refunded.** Status: ✅ fixed 2026-10-06 (session D): a `/v1/historical/range` connection dropped after the request was sent is retried once (`LARGE_CALL_DROP_RETRIES`), then `dropped` (recorded by the backfill); connect failures keep the normal retries.
- Cause: `src/nqbot/clients/skylit.py:229` treats a dropped connection as refunded. A read error after the server answered 200 is probably charged (25 credits), and the retry pays again.
- Fix: ask Skylit, or retry a large range call only once per error.

**D8. LOW: step-7 refusals don't catch a plain `SkylitError` or `CacheMismatch`.** Status: ✅ fixed 2026-10-06 (session D): step-7 `SkylitError`/`CacheMismatch` print a plain message and a hint; `darkpool --date` before month 3 warns, the help and checklist say month 3 or later.
- Cause: the refusal tuple at `src/nqbot/cli.py:254-255`. A traceback appears for a 504 "gave up" on the big full-chain `strike-window` call. It may also appear for `darkpool --date` in month 1–2, whose 45-day lookback may fall outside the 365-day reach (unverified).
- Fix: catch `SkylitError`/`CacheMismatch` and print a hint. Advise a `--date` in month 3 or later. Consider a shorter `--minutes` option for `strike-window` in case the full-chain call times out.

**D9. LOW: `gameplan-batch` reads only the top level of `raw/gameplans/`.** Status: ✅ fixed 2026-10-06 (session D): `gameplan-batch` reads one folder down (never `replaced/`) and prints the absolute folder and file count.
- Cause: `src/nqbot/gameplan/parser.py:432`. A Drive folder zip probably extracts into a `Jeron gameplans\` subfolder (unverified), and the batch then says "0 files" with no hint.
- Fix: warn about subfolders that hold files (or read one level down), and print the absolute folder path.

**D10. LOW: the docs never list what the owner must pick or edit by hand.** Status: ✅ fixed 2026-10-06 (session D): CLAUDE.md SSD-day checklist (hand picks and edits, Modern Standby check before the backfills, the gameplans folder on the SSD); `.env.example` (unignored).
- Cause: nowhere says:
  - to choose the busy day and the 5 month-6 sample days before `strike-window`, and reuse them for `sample-days` and the Atlas check;
  - which `darkpool --date/--check-dates` and `parity --session` values to use;
  - which settings to edit and commit: `skylit_max_strikes` and `map_latency_sec` in settings.yaml, `dp_lag_sessions` and `tempest_lag_sessions` in features.yaml;
  - that the Modern Standby check with `run dummy` takes both locks, so it goes before the backfills;
  - that `data/raw/gameplans/` means `<SSD>\nqbot\data\raw\gameplans`, not the repo.
  
  There is also no `.env.example`.
- Fix: an SSD-day checklist in CLAUDE.md, and a `.env.example` listing the three API keys.

**D11. LOW (sealed months): `build_day_outputs` prints every roll spread value to the console and log, including the Sep-2026 sealed-month roll.** Status: ✅ fixed 2026-10-06 (session D): the roll log says only that the spread was measured, never its value.
- Cause: `src/nqbot/clients/databento_pull.py:549`.
- Fix: log the roll without the number, or show the number only for development months.

**D12. LOW: the "back up after warm-up + months 1–6" step needs the backfill stopped (Skylit lock).** Status: ✅ fixed 2026-10-06 (session D): one backup when step 10 finishes (CLAUDE.md, SPEC §3.1 and §11, `backup.py`); `repull/` is now backed up last, with the other folders lock-free checks write. **Check D (2026-10-06):** the CLAUDE.md Commands line still said to back up after the warm-up and months 1–6; fixed.
- Cause: `nqbot backup` takes the Skylit lock, and so does the backfill. The whole backfill probably finishes in about 1–2 nights anyway (unverified).
- Fix: one backup when step 10 finishes, or let the backup run without the lock once decision 2's own-files rule makes that safe.

### Owner decisions (2026-10-06)

1. **Pack credits.** The owner checks on Skylit's site whether pack credits add to the API balance and whether they expire, and buys the pack on SSD day after the step-7 estimate. The backfill prints the balance before it starts and stops cleanly (status `credits`, no crash) if the balance runs out, so the owner can top up and re-run.
2. **Alongside checks (D3, D12).** The short checks (stream recording, parity, late prints, Tempest) run alongside the backfill without its lock, as long as each writes only to its own files and the backfill's credit guard leaves enough credits for them. Step 10 never waits for a market session. Add `nqbot stop <job>`, which finishes and saves the current call before stopping.
3. **Strike window (D2).** `skylit plan` refuses outright until the strike-window check passes, with no override flag. If it fails, the fix is to raise `skylit_max_strikes` and re-check.
4. **Failing windows (D1).** Skip a window that keeps failing, save its raw answer, record it so it is never re-pulled, and carry on. Stop only after K failures in a row (K in config). Retry all skipped windows once at the end of the run, and list any still missing in `coverage.md`.
5. **Laptop (D5, D6).** Add a Windows Defender exclusion for the SSD folder and pausing Windows Update for 1 week to the SSD-day checklist.

### Owner decisions after session D (2026-10-06)

1. **Retries.** Failed windows are retried at the start of every run and at the end of every night, oldest first, so nothing leaves the 365-day reach while it waits; a window still failing when it leaves the reach is recorded as lost in coverage.md. (Implemented: `at_start` / `at_end` hooks in the runner, `SkylitBackfillJob._retry_pass`; timeouts in a row end a pass, never the run, and refusals don't count.)
2. **Strike window.** Zero tolerance stays (every board on every sample day, as the SPEC says), no allowed-share setting. The report shows the board(s) that set the proposal and the next smaller window with their cost (credits are the same per window; the data size differs); the owner decides.
3. **Oldest-window test.** A rerun may pay the 25 credits again.
4. **Commits.** Commit straight to `main` (solo project); added to the CLAUDE.md working rules.

### Owner decisions on the second review (2026-10-06)

1. **Dropped connections.** Losing history is worse than a few credits: a window whose connection dropped gets up to 3 more tries (`skylit_dropped_extra_tries`) while still inside the 365-day reach, in the end-of-night retry pass, oldest first, spaced out (1, 5, 15 minutes between rounds), each try sent once (25 credits at most), within a nightly cap (`skylit_dropped_retry_credits_per_night`, 500). coverage.md lists every retried window and its credits. Given up only once it leaves the reach or uses up its tries. The owner asks Skylit whether a dropped call after a successful answer is charged; if not, the cap can go.
2. **Strike-window timeouts.** Never a shorter sample: a timed-out full-chain call is split into consecutive shorter calls (3 × 5 minutes, then 5 × 1 minute) that together cover the same full 15 minutes; a manifest records the pieces and the check refuses unless they cover all of it.

### Check D (2026-10-06)

A fresh agent that hadn't seen the work checked D1–D12 against the code and tests (not the summaries). D2–D11: fixed. D1: not fixed for dark-pool dates; D12: a stale backup line in CLAUDE.md. Both fixed the same day (472 tests pass):

- **D1, dark pools and Tempest.** Once a dark-pool page is paid for, any failure keeps every page answered in `skylit/failed/` (`DarkPoolFailed`, `step7.keep_dp_failure`); a conversion or write failure (any exception) is recorded in `failed.json` and the night carries on, like a window. A failed 31-day call records all its dates with that one answer, so none is paid twice. The retry passes convert a complete kept copy again for free. The Tempest call follows the same rule (`TempestFailed`). Tests in `tests/test_presd_session_d.py`.
- **Owner decision (2026-10-06): one paid re-pull of an unusable kept copy.** A conversion bug is fixed in code and converted again from the kept copy for free. If the kept copy itself is incomplete or corrupt (cut off part-way, out of time order, or unreadable), the window, dark-pool date or Tempest range gets **one** paid re-pull while it is still in reach, within `skylit_dropped_retry_credits_per_night` (shared with the dropped-window tries; else it waits for the next night). It is marked used before the call and listed with its credits in coverage.md. Still failing when it leaves the reach: lost.
- **D12.** The CLAUDE.md Commands line now says to back up once, when step 10 finishes.
