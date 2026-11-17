# Step 14, build stage 5: the Gameplan page (2026-11-16)

The last of five stages (plan: owner 2026-11-15; SPEC §9b item 4, §3.4, §5.6; `design-system/pages/gameplan.md`).
**Approved by the owner 2026-11-17** after the first build and two rounds (their sections follow below); every round
committed locally, nothing pushed yet.

## Final state (2026-11-17)
- **Server** (`src/nqbot/ui/api_gameplan.py`): the day's rules (setup windows, last entry, flat-by, an early close, the
  news blackouts with their releases); upload to a staging folder with a free cost estimate; the paid parse only on the
  owner's click on the worst-case button, within `gameplan_max_usd_per_file` (above it, the CLI), never retried by
  itself, the actual cost shown after it; the same `parse_file` and gameplan lock as `nqbot gameplan`; edits as owner
  overrides (side, zone, trigger, ES gates, stop, targets, invalidations per entry; HVL, HVL0, regime line; scenarios'
  probability, bias and text; the plan-wide conditions), each settling exactly the review flags shown on its field;
  "OK as parsed"; Confirm (before 09:30 ET only with no open review flag; it counts for the day's trading only then;
  any edit or OK after it asks for a new one); ledger months 7–12 hidden until gate 16; an offline parser stand-in on
  the synthetic panel.
- **Page** (`ui/web/src/pages/gameplan/`): the title names another day (NOT TODAY); status, flags-to-review and the next
  session mark on the Flip-Disk Matrix; the day timeline; the drop zone with the estimate, Parse / Parse again, Replace
  and the actual cost; the editable entries table (flagged fields outlined with their flag and an OK-as-parsed button),
  scenarios, plan-wide conditions, levels with tags, flags, the pre-open chart note, the Confirm bar; the panel's own
  Day picker (grouped by month, today first with its state, keyboard-usable); day switches that load first, crossfade
  and glide (250 ms, Calm instant), the scroll held; `/gameplan?date=…` opens a day.
- **Shared:** `components/ui/option-picker.tsx` (no browser `<select>` left in the app), `AutoHeight` gliding from the
  first change (explicit height, CSS transition; Performance's tab switch checked).
- **Checks:** `ui/web/scripts/shoot.mjs` stages 5, 5b, 5c, 5s on fresh `--synthetic` panels (5r reads the owner's real
  stored days, read-only; not run since the owner asked for synthetic-only screenshots); tests
  `tests/test_ui_gameplan.py` (17), `tests/test_tool_guard.py`.

## Open items carried forward
1. **Step 22:** the pre-open NQ chart with the gameplan's zones; the bot's own levels in the Levels list ("bot"); the
   live engine reads only a gameplan confirmed before 09:30 ET (it keeps that copy for the day; a later confirmation
   or edit is saved for training only).
2. **The local hook** `.claude/local/no_test_pipe.py` (wired in the uncommitted `.claude/settings.local.json`, owner
   2026-10-31) checks rule 14 (e) for Bash only and is now covered by the committed hook; removing it is the owner's
   choice (it also blocks quoted examples of a piped test run, which the new hook allows).
3. Everything from `build_stage_4.md`'s list: 4× replay frame times after 13c; the pre-13d member record and the
   retested-extreme age fix; the real review round after 13d, spread by month; the full suite, then push all local
   commits, after 13c.


**Evidence** (`ui/web/scripts/shoot.mjs`):
- [`build/stage5/`](build/stage5/) ([`checks.json`](build/stage5/checks.json)), on a fresh `--synthetic` panel at 1440,
  1280 and 960 px: all checks passed:
  - the empty page, the day timeline, and a day with a news blackout (CPI 2025-08-12);
  - dropping a file → the cost estimate → Parse → the table;
  - an edited stop that is still there after a reload;
  - Confirm, and a Calm shot;
  - no request left 127.0.0.1, no console error, nothing scrolls sideways (at 960 px only the entries table scrolls,
    inside its panel).
- [`build/stage5r/`](build/stage5r/), on your real panel, **read-only** (no upload, edit or confirm): your stored 2026-10-06
  gameplan, 11 entries, 6 review flags and 4 info, as confirmed on 2026-10-07.
- Tests: `tests/test_ui_gameplan.py` (9).
- **Nothing was paid:** the synthetic panel uses an offline stand-in for the Claude API, and the tests use the parser
  tests' fake client.

## How to look at it
```
uv run nqbot ui build
uv run nqbot ui              # Gameplan is key 2 / the dock's map icon
uv run nqbot ui --synthetic  # to try the upload, parse, edit and confirm without touching your gameplans or paying
```
Your panel on 8733 is already restarted on this build.

## What was built

### Server (`src/nqbot/ui/api_gameplan.py`)
- **`GET /api/gameplan[?date=D]`** (default: today in New York):
  - the day's rules: setup windows, last entry and flat-by (an early close moves flat-by to its close − 1 min);
  - the news blackouts, each with its release and its no-entry span (30 min + 30 s before, 60 after); live-only
    red-folder extras are marked;
  - the effective gameplan (the parsed file with your overrides on top, as everything downstream reads it), its
    flags, your edits, the status and the stored days.
- **Upload (`POST /api/gameplan/upload`):**
  - the file goes to a staging folder on the SSD (`tmp/gameplan_uploads/`), never into the archive yet;
  - it is converted (.md, .pdf, .docx) and its cost estimated with the **free** token count (rule 2);
  - nothing is parsed at this point.
- **Parse (`POST /api/gameplan/parse`):**
  - only with your approval of the printed worst case, which is the **Parse · up to $0.34** button itself;
  - at most `gameplan_max_usd_per_file` ($1.00); above it, the page shows the CLI command with `--approve-usd`;
  - it runs the same `parse_file` as `nqbot gameplan <file>`, under the same gameplan lock;
  - a day that already has a gameplan is replaced only when you tick **Replace**;
  - a file already parsed is never sent again ("already parsed for D", no estimate, no call).
- **Edit (`POST /api/gameplan/edit`):** one owner override per edit (`gameplan/overrides.py`), so a re-parse never loses
  it.
  - Editable: per entry the side, zone, trigger price, stop and targets; plan-wide HVL, HVL0 and the regime line.
  - Entries are found again by their label, else by side plus level, as the archive's overrides are.
- **Confirm (`POST /api/gameplan/confirm`):** records your confirmation with an optional note.
  - It **counts for the day's trading only if given before 09:30 ET** of the gameplan's own day (§9b item 4).
  - Any edit after it shows "confirm again" and no longer counts.
- **Synthetic panel:** a temporary gameplan store and a canned answer (`synthetic_gameplan.response.json`): no network, no
  cost, your gameplans untouched.

### Page (`ui/web/src/pages/gameplan/`)
- **Header:** the status line, e.g. "Today: confirmed 09:12", "No gameplan today: the bot runs without it", or "confirmed
  2026-10-07 19:59 (after 09:30 ET that day: saved for training only)". A **Day** picker lists today and every stored
  gameplan.
- **Three tiles:** Status; Flags to review (with the info count); Next, the time to the next session mark (open, golden
  zone, all setups, last entry, flat-by) on the Flip-Disk Matrix, recomputed from the clock every second.
- **Day timeline,** 08:00–16:15 ET: the golden-zone and all-setups bands, news blackouts hatched with their release
  line, open / last entry / flat-by marks, and an ember "now" line on today; the times listed under it.
- **Drop zone** (the joyco File Dropzone 19201 pattern, its missing upload hook written here): drop or click; then the
  estimate card with the tokens, the worst case, the per-file limit, the after-09:30 notice, Replace when needed,
  Cancel and Parse.
- **Entries table:**
  - HVL, HVL0 and the regime line on top;
  - then Entry (sticky), Side, Zone, Trigger, ES gates, Stop, Targets, Invalidation;
  - click a value to edit it (Enter saves, Esc cancels);
  - fields with a review flag carry a warn left rule and the flag's text;
  - the conditions written on the entries fold out below.
- **Scenarios** by frame with probability and bias; the plan-wide conditions fold out.
- **Levels,** high to low, with tags (side, zone or trigger type, "gameplan"); the bot's own levels join at step 22.
- **Flags** (review first, then info) and your edit count.
- **Pre-open chart:** a note that it joins at step 22.
- **Confirm bar** with an optional note.

## Choices I made (say if any is wrong)
1. **The approval of a paid parse is the Parse button showing the worst case** (within the $1.00 per-file limit, as for
   `nqbot gameplan`). Above the limit the page doesn't parse; it shows the CLI command.
2. **Past days are viewable and replaceable.** The Day picker lists every stored gameplan, including your August and
   September files, which sit in sealed ledger months 11–12. The page shows only the gameplan as you wrote and confirmed
   it: no prices after the fact, no outcome. Say if you'd rather the picker hide sealed months.
3. **Confirming is allowed with review flags left** (you confirmed the archive with model notes accepted as stored); the
   tile shows how many remain.
4. **The page title stays "Today's gameplan"** when another day is picked; the status line and the file panel name the
   day.
5. **Not editable here yet:** ES gates, invalidations, scenarios and their probabilities, and the plan-wide conditions
   (shown read-only). I can add any of them.

## Open items carried forward
- The pre-open NQ chart with the zones, and the bot's own levels in the list: step 22.
- Everything from `build_stage_4.md`'s list (4× frame times after 13c, the pre-13d items, the real review round after 13d,
  the full suite and push after 13c).

## Your stage-5 answers and notes (2026-11-16/17)
**Evidence** (all on fresh `--synthetic` panels, as asked): [`build/stage5/`](build/stage5/),
[`build/stage5b/`](build/stage5b/), [`build/stage5s/`](build/stage5s/), all checks passed; `stage3m` (Performance's tab
glide) re-ran clean with the new AutoHeight. Tests: `tests/test_ui_gameplan.py` (16).

1. **Approval and cost.** The worst-case button is the approval, within $1.00. A failed parse is never retried by
   itself: the file stays staged and the button turns into **Parse again · up to $0.34** (another click, another paid
   call); the error says so. After a parse the file panel shows the **actual cost** beside the estimate ("actual cost
   $0.00 (estimate up to $0.34)" on the synthetic panel, whose stand-in costs nothing).
2. **Months 7–12 hidden until gate 16.** They are left out of the Day picker and every route refuses them (403, with
   the reason), uploads included; once gate 16 is in `config/gate_record.json` they come back. Tested. **What opened
   gameplans from those months:** no check, screenshot or test of this stage did (the tests use the fixture's
   2026-03-11/12 and a stub file in a temporary folder; the synthetic panel uses today and tomorrow; the one real-panel
   check, read-only, opened 2026-10-06, after the window). But while building the page, before your answer, I read
   your stored 2026-09-16 gameplan (ledger month 12) directly to learn the stored shape (an entry, a scenario set, its
   flags). Nothing from it went into a report, test or screenshot.
3. **Confirm before 09:30** needs every review flag fixed or marked **OK as parsed** (a button on each review flag, in
   the table and in the Flags panel); after 09:30 (training only) open flags are allowed. Tested both. An "OK as parsed"
   is kept with your edits, matched by the flag's field and exact text, so a re-parse that words a flag differently
   shows it again; marking one after a confirmation asks for a new confirmation.
4. **Another day** is named in the title ("Gameplan for Wed, Nov 18, 2026") with a **NOT TODAY** badge and a subtitle
   (past: "for checking and training only"; coming).
5. **Editable now:** the ES gates ("ES > 6,812; ES < 6,790"), the invalidations ("1m close above 24,560"; instrument,
   direction, price and timeframe read from the text) and each scenario's probability, bias and text. Same edit layer:
   kept apart from the parsed file, outlined when flagged, a new confirmation after an edit. I read "scenario
   conditions" as the scenarios themselves; the plan-wide conditions list stays read-only (say if you meant that list).
   - **Each edit settles exactly the review flags shown on the field it edits** (`resolves` on the override). The older
     rule settled every flag under the edited path, so an ES-gate edit would also have settled a flag on the trigger
     price; your archive's overrides keep the older rule.
6. **The Day picker** is the panel's own dropdown (`components/ui/option-picker.tsx`, the month picker's pattern and
   styling): today at the top with its state, then the stored days grouped by month, newest first, each with a state
   chip; ↑ ↓ Home End, Enter / Space, Esc, Tab. The inline side and bias choices use it too. **No browser `<select>` is
   left** in the app or on the emergency page. The page also opens a day from its address (`/gameplan?date=…`).
7. **Day switches** load the new day first (the old one stays meanwhile), crossfade, and glide the height (250 ms
   ease-out) with the scroll held; Calm is instant. Measured from a day without a gameplan to one with, and back: 12
   and 14 in-between heights, no blank frame, the scroll steady at 180 px; Calm: none in between.
   - **Found and fixed in the shared AutoHeight:** the first change after a page opened snapped instead of gliding
     (Motion had no starting height to animate from); it now sets an explicit height first and glides with a CSS
     transition. Performance's tab switch glides as before (stage 3m).
   - **Also fixed:** the dropdown's focus going back to its trigger scrolled the page to the top; and opening
     `/gameplan?date=…` flashed today's data first.

## Round 2 (owner 2026-11-17)
**Evidence:** [`build/stage5c/`](build/stage5c/) (all checks passed, fresh `--synthetic` panel); tests
`tests/test_ui_gameplan.py` (17) and `tests/test_tool_guard.py`.

1. **Plan-wide conditions editable:** click one to edit it, empty it to remove it, "+ Add a condition" adds one; stored
   as one override of the whole list (the parsed file untouched), outlined with its flag when flagged, and an edit
   after a confirmation asks for a new one. Tested; on the synthetic panel a condition was added, edited and still
   there after a reload.
2. **Sealed-month read logged** in SPEC §7 rule 2b's new sealed-month access log (the 2026-09-16 gameplan, month 12,
   opened 2026-11-16 to learn the format; nothing from it went into code, tests, reports or screenshots); from now on
   such checks use months 1–6 or synthetic examples only (also kept in Claude Code's memory for later sessions).
3. **PreToolUse hook** (`.claude/settings.json` → `uv run --quiet python -m nqbot.tool_guard`, on Bash and PowerShell;
   no secrets): blocks a piped test run (rule 14 (e)) and stopping processes by name or pattern (rule 15: `taskkill
   /IM` or `/FI`, `Stop-Process -Name`, anything piped into `Stop-Process`, `pkill`, `killall`, `kill $(pgrep …)`,
   `xargs kill`, `wmic process … delete`, `Win32_Process … Terminate`), naming the rule; by PID stays allowed; quoted
   text is ignored. Results on harmless examples (fed to the hook as Claude Code sends them; nothing ran):

   | Verdict | Rule | Tool | Command |
   |---|---|---|---|
   | BLOCKED | 14 (e) | Bash | `uv run nqbot test tests/test_config.py \| tail -5` |
   | BLOCKED | 14 (e) | Bash | `python -m pytest -q 2>&1 \| tail` |
   | BLOCKED | 14 (e) | PowerShell | `uv run nqbot test \| Select-Object -Last 5` |
   | BLOCKED | 15 | Bash | `taskkill //IM node.exe //F` |
   | BLOCKED | 15 | Bash | `taskkill /F /FI "IMAGENAME eq python.exe"` |
   | BLOCKED | 15 | PowerShell | `Stop-Process -Name python` |
   | BLOCKED | 15 | PowerShell | `Get-Process python \| Stop-Process` |
   | BLOCKED | 15 | Bash | `pkill -f nqbot` |
   | BLOCKED | 15 | Bash | `killall node` |
   | BLOCKED | 15 | Bash | `kill -9 $(pgrep -f uvicorn)` |
   | allowed | | Bash | `uv run nqbot test tests/test_config.py` |
   | allowed | | Bash | `uv run nqbot test \|\| echo failed` |
   | allowed | | Bash | `grep -n "nqbot test" CLAUDE.md \| head -5` |
   | allowed | | Bash | `taskkill //PID 1234 //T //F` |
   | allowed | | PowerShell | `Stop-Process -Id 4321 -Force` |
   | allowed | | Bash | `kill 1234` |
   | allowed | | Bash | `git log --oneline \| head -3` |

   Live in the session: `false && taskkill //IM zzz_no_such_process.exe //F` was refused before running with the rule-15
   message.
