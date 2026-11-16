# Step 14, build stage 2: Home, Accounts, Settings (2026-11-15)

The second of five stages. Stage 1 is committed locally (97fef97); this stage is not committed yet and stops for your
comments. Screenshots and measured checks: [`build/stage2/`](build/stage2/) ([`checks.json`](build/stage2/checks.json)),
from `ui/web/scripts/shoot.mjs 2` against the running panel.

**That run:** all checks passed.
- No request left 127.0.0.1, and there was no console error or warning. The 428 behind the raising-risk dialog is the
  server's expected answer.
- FLAT ALL stayed on one line, and nothing in the top bar overflowed.
- Home showed all six nodes at 1440, 1280 and 960 px, none over the date block.
- The screenshot run wrote nothing to your panel file. Its change log still holds only the seeding entry.

## Your draft accounts are ready to check
The first time the panel ran, it created `D:\nqbot\data\panel\accounts.yaml` with two drafts:
- **`mffur1`:** MFFU Rapid EOD 25k evaluation, webhook variable `TP_WEBHOOK_MFFUR1`.
- **`mffub1`:** MFFU Builder 25k evaluation, webhook variable `TP_WEBHOOK_MFFUB1`.

Both are `draft: true`: they are not traded or simulated until you open each one, check it against the firm's site, and
press **Save as checked**. Pro 50k and Lucid Flex 25k are in the preset picker (evaluation and funded), as are the
Rapid EOD and Builder funded and after-first-payout stages.

## What was built

### Python (`src/nqbot/accounts/`, `src/nqbot/ui/`)
- **`accounts/schema.py`:** a pydantic model of every §6.7 field, plus `draft`, `preset`, `payouts`,
  `first_funded_trade_date` and `pending_risk`.
  - **Names:** lowercase letters and digits only.
  - **Webhook:** stored only as the `.env` variable name (`TP_WEBHOOK_…`).
  - **Secrets:** every text value is checked for URLs, keys and logins, and refused.
- **`config/presets.yaml` + `accounts/presets.py`:** every §6.7 stage.
  - Rapid EOD 25k and Builder 25k: evaluation, funded and after first payout.
  - Pro 50k and Lucid Flex 25k: evaluation and funded.
  - A test compares each stage with the SPEC table, field by field.
- **`accounts/derive.py`:** the live-as-you-type values and the §9b checks.
  - **Values:**
    - the daily profit limit;
    - risk per trade against its 1/N cap;
    - lives (`lives_dust_usd`);
    - the smallest stop with full risk before the size cap binds;
    - the largest stop for one micro, or one mini when micros are off.

    The stop values follow the §6.1 all-in formula, for a limit entry and for a marketable or stop-limit entry.
  - **Checks:** errors block the save (the fraction, risk above drawdown ÷ N, missing fields, a duplicate name, two
    active accounts resolving to the same webhook URL). Warnings don't block (a bot daily loss cap above the firm's DLL,
    a 4th MFFU or 6th Lucid account with copies counted).
  - **Webhook check:** it compares `.env` values and never shows one. On this PC it says "not available on this
    machine".
- **The raising-risk dialog's numbers** show:
  - the new risk in dollars;
  - lives before and after;
  - the losses that fail the account;
  - for four stops at the 2.5R baseline, what the max position, the daily profit cap and the DLL leave of the risk.
- **`ui/panel_store.py`:** the panel's files on the SSD (`<data_dir>/panel/accounts.yaml`, `presets.yaml`, `ui.yaml`).
  Every write:
  - takes a lock and re-checks the hash the page loaded (a stale page gets a 409 and reloads);
  - copies the previous version to `panel/history/`;
  - is an atomic replace;
  - is logged to `<root>/logs/ui_changes.jsonl`.

  Edits are refused while the engine is down mid-session (the hook for step 22).
- **`ui/api_panel.py`:** `/api/accounts` (list, check, save, delete), `/api/presets` (save, delete), `/api/settings`
  (read, save per account), `/api/ui` (Effects) and `/api/home`.
  - **Raising risk:** the server refuses the save (428) until the typed `<name> 1/N` arrives. Then it stores the raise
    in `pending_risk`, and the account keeps trading at its current fraction until the sweep.
  - **Lowering risk:** applies at once and cancels a pending raise.
- **`nqbot ui backup` (`ui/panel_backup.py`):** `rclone copy --checksum`, then `rclone check`, of `data/panel/` to Drive
  `NQ bot backup/panel/`.
  - **Lock:** its own, never the Skylit job lock.
  - **Hourly:** `nqbot status-push` also runs it, once the build pin moves after 13c.
- **The Effects setting** is now saved in `data/panel/ui.yaml` and carried in the status stream.

### Home
- The approved order-flow scene, moved from the mock, with the forming title, the nodes arriving clockwise and arcs to
  the centre.
- The node cards show live numbers from `/api/home`:
  - Gameplan: the next setup window, from your fixed windows.
  - Settings: risk per trade.
  - The rest show "—" with the reason: balances come with the engine (step 22), review cards with stage 4, P&L with the
    step-15 ledgers.
- Keys 1–6 never fire while typing or while a dialog is open.
- **Date block:** the scene leaves the bottom band free for it.
- **Effects budget, measured idle on Home over 8 s:**
  - **Full: 7.8%** of a core (limit 15%). A first measurement was 15.3% while 13c's builds shared the CPU, so once the
    title has formed, its idle particle flow now draws at 30 fps. (Since reverted to 60 fps at the owner's request, see
    below.)
  - **Calm: 0.4%** (limit 3%).

### Accounts
- **Card grid** (3 across at 1440 px, 2 at 1024). Each card shows:
  - the name, the preset and stage, and state badges (DRAFT, EVALUATION / FUNDED, DEMO, INACTIVE);
  - the drawdown rule in words and lives as pips;
  - risk per trade and the daily profit limit;
  - today's est. P&L and drawdown left as "—" until step 22.

  The tilt and spotlight run in Full only.
- **Edit form** at `/accounts/<name>`:
  - the preset picker (grouped by firm; fills every rule field, keeps the name, webhook and copies);
  - the six §9b groups, as bordered panels, with fields that don't apply hidden (e.g. payout fields on an
    evaluation);
  - a sticky live summary;
  - inline errors, and an error summary that links to the fields;
  - the unsaved-changes bar ("Save as checked" on a draft);
  - Save as preset, the raising-risk dialog, and delete (type the name, then hold for 1.2 s);
  - leaving with unsaved changes asks first.

### Settings
- **Layout:** a sticky section list that follows the scroll, and an account switch.
- **Risk profiles:** five cards, each with its multiplier from settings.yaml. Their backtested stats read "n/a" until the
  step-15 sweep.
- **Custom:** the risk $ slider, capped at drawdown ÷ N (the cap is drawn, and choosing Custom pre-fills it), plus the
  min EV and min probability sliders.
- **Per account:** auto-protect (funded accounts only), lives mode, λ, and Re-run sweep (disabled until step 15).
- **Global (read-only, owner rules):** `gate_pass_rate_min` and the session windows, as locked rows. The rule they come
  from is named, e.g. golden zone from 09:45, all setups from 10:00, last entry 15:45, flat by 15:59, blackout 30 / 60.
- **Effects & display:** Auto / Full / Calm, saved to the SSD.
- **Danger zone.**
- **Saving:** changes go through the unsaved-changes bar and apply between trades only.

### 21st.dev components
Code read for each; rows in `design-system/coverage.md` §3, stage 2.
- **Used and adapted:**
  - Hold to Confirm 23527
  - Unsaved changes 9576
  - Segmented Control 44919 (without its Radix and missing helper)
  - Switch 11886 (on a plain button)
  - Marks Slider 29217 (with a cap marker)
  - Progress Bar 9659 (lives pips)
- **Moved from the mock:** Tilt Card 12244 and Halftone Wordmark Footer 37628 (its `<style>` moved into the stylesheet
  for the CSP).
- **Built in a component's pattern:** the preset picker (Combobox 34727).
- **Not usable:** Meter 38114, whose helper files weren't returned.

## One finding for you: the daily cap already limits risk on the Rapid EOD evaluation
The raising-risk dialog shows it. With a $450 daily cap, a trade aiming at 2.5R can risk at most $450 ÷ 2.5 = $180, at
every stop. So on that account the 1/3 risk of $333 is already cut to $180 for 2.5R trades, and 1/2 would change
nothing there (§6.1: "reduce if the target would exceed the remaining daily profit cap"). Nothing to decide now; the
step-15 simulator will show it per account.

## Tests (targeted, rule 14 (b))
- **New test files:**
  - `tests/test_presets.py`: every stage equals the SPEC table, and is a valid account.
  - `tests/test_accounts_derive.py`: the hand-worked $450, $333, 3 lives, 7.25 and 164.5 pts, every stage's daily
    limit, the size caps, the checks, the warnings, the webhook check without showing a URL, the raise effect.
  - `tests/test_accounts_schema.py`: names, the webhook variable, secrets refused.
  - `tests/test_panel_store.py`: the drafts seeded once, the hash check, history, the change log, no SSD, edits
    refused mid-session, the API (the typed confirmation, the pending raise, lowering cancels it), presets, settings,
    delete, Effects, Home, and the backup with a fake rclone.
- **Result:** with the stage-1 UI tests, `test_cli`, `test_risk` and `test_config`: 127 passed, `PYTEST EXIT CODE: 0`.

## Screenshots (`build/stage2/`)
| Shot | Shows |
|---|---|
| `home_1440`, `_1280`, `_960`; `home_intro_1440` | Home with the Accounts card hovered; the intro mid-way |
| `accounts_1440`, `_1280`, `_960`; `accounts_card_hover_1440` | the two drafts; tilt and spotlight |
| `account_form_1440`, `_960`; `account_form_limits_*`; `account_form_funded_1440` | the draft's form and summary; position limits; the funded fields appearing |
| `preset_picker_1440`, `save_as_preset_1440` | the picker grouped by firm; the Save as preset dialog |
| `raise_risk_1440` | 1/3 → 1/2: the dialog with the typed confirmation (refused before writing) |
| `account_invalid_960` | risk $900: the inline error and the blocked save bar |
| `account_delete_1440`, `leave_guard_1440` | delete (type, then hold); leaving with an unsaved change |
| `settings_1440`, `_960`; `settings_custom_*`; `settings_global_*`; `settings_footer_1440` | profiles, Custom sliders with the cap, locked rows, the footer |

## Notes
- Your real file was written once, by the seeding. The screenshots wrote nothing to it.
- The Home node cards near the left edge open their hover card under the edge of a 960-px window (about 2 px from it),
  as in the mock. Tell me if you'd like the left-column cards to open rightward.

## Next: stage 3 (Performance, in full)

## Your stage-2 changes (2026-11-15)
Screenshots and checks: [`build/stage2b/`](build/stage2b/) ([`checks.json`](build/stage2b/checks.json)); video
`docs/design/video/stage2b/bar_follows_dock_and_hover_1440.webm`. **That run: all checks passed**, and it wrote nothing
to your panel files. The preset delete and Undo were shown against faked endpoints; the real ones are covered by
`tests/test_panel_store.py`.

1. **The unsaved-changes bar follows the dock.**
   - **Positions:** 118 px from the bottom while the dock is open, 16 px while it is minimized.
   - **Motion:** it slides between the two in 220 ms with an ease-out, instantly in Calm.
   - **Measured at 1440 and 960 px:** open 118 px, minimized 16 px, back to 118 px. Low, it clears both the date block
     and the dock's circle.
   - **Shots:** `bar_dock_open_1440`, `_960`; `bar_dock_min_1440`, `_960`.
2. **Dock hover.**
   - **Magnification:** now up to 1.2× (40 → 48 px, measured 1.2), a third of the earlier jump.
   - **Labels:** the dock now sits on a layer above the bar, and the open-dock position leaves room for the labels.
     Measured: the hovered label never overlaps the bar.
   - **Shots:** `dock_hover_bar_1440`, `_960`.
3. **Deleting one of your presets.**
   - **Picker:** each of your own presets has a delete icon; the built-in firm presets have none. Pressing it turns the
     row into "Delete …? Keep / Delete".
   - **Undo:** after the delete, a toast offers Undo for 10 s, which saves the preset back.
   - **Shots:** `preset_own_*`, `preset_delete_confirm_*`, `preset_deleted_undo_*`.
4. **The editor's bar: Reset, Discard, Save.**
   - **Reset** undoes every change and stays in the editor. Checked: the field went back and the URL stayed.
   - **Discard** undoes every change and closes the editor. Checked on a new account: back at `/accounts`, no account
     created (3 cards before and after).
   - **Save** reads "Add account" for a new account and "Save as checked" for a draft. Settings has Reset and Save.
   - **Shot:** `bar_buttons_1440`.

**A stage-1 bug found here.** The toasts are drawn outside the frame that sets their `--toast-bottom` lift, so they
always sat 14 px from the bottom edge, and at 960 px the Undo toast overlapped the dock.
- **Fix:** the lift is now set on the page root, and toasts also keep above the unsaved-changes bar while it is open.
- **Check:** a new one in the script, "toast clear of the dock", passes at both widths.

**Seen in your panel files** (your own use, not the scripts):
- You saved `mffub1` and `mffur1` as checked, added `mffur2`, and saved the preset "Mffu 50k (evaluation)".
- Effects was clicked through Full → Calm → Auto at 17:12 ET.
- With `mffur2`, the screenshots' one-field change (`copied_accounts` 1 on `mffur1`) shows the 4th-MFFU-account
  warning. That change was never saved.

## The top-bar account switcher (2026-11-15)
Screenshots and checks: [`build/stage2c/`](build/stage2c/) ([`checks.json`](build/stage2c/checks.json)). **That run: all
checks passed.**

- **Opening it:**
  - **Hover:** after 150 ms. Measured: closed at 80 ms, open at 330 ms. It stays open while the pointer moves into the
    list, and closes once it has left both.
  - **Click:** opens it too.
  - **Keyboard:** Enter, Space or ↓ on the trigger opens it; ↑ ↓ / Home / End move; Enter focuses that account; Esc
    closes and returns to the trigger.
- **One row per account; the list scrolls past 5 rows.**
  - **First line:** name, type badge (Rapid, Builder, Pro, Lucid Flex, or a saved preset's name) and stage (EVAL /
    FUNDED), then status: active, paused, blown, inactive, or "draft — not checked" in muted italics.
  - **Room to the floor:** the $ distance with a thin bar, green at 50% or more of the drawdown, amber below 50%, red
    below 25%.
  - **Third line:** today's P&L with its sign and arrow; progress to the profit target (evaluation) or the next payout
    (funded); lives left.
  - **Before step 22:** today these show "—" (a dotted empty bar), because balances, floors and P&L come with the engine.
    The target is filled in (e.g. "/ $1,500").
- **Footer:** "Manage accounts" opens the Accounts page; "+ Add account" opens the new-account form (checked:
  `/accounts/new`).
- **Focus only:** picking a row makes that account the panel's focus. The list says so in a small note: "It never
  changes which accounts trade." The focus is kept in this browser; from stage 3, Performance follows it. The command
  palette's account entries set the focus too.
- **Shots:**
  - `switcher_open_1440`, `_960`: your three accounts, opened by hovering.
  - `switcher_keyboard_1440`, `_960`: opened with ↓, the second row highlighted.
  - `switcher_preview_live_1440`: a **preview**, with 7 made-up accounts and live numbers injected into the browser's
    status stream only. It shows the scrolling, a paused account, and the bar at green ($880 of $1,000), amber ($420)
    and red ($190).
- **Server:** the status's account summary now carries each account's type, drawdown, target, and a `live` block that
  is `null` until the engine fills it; a new test checks that nothing is invented. The real panel files were not
  written.

## Owner's changes after the switcher (2026-11-15)
- **Evidence:**
  - Screenshots and checks: [`build/stage2d/`](build/stage2d/) ([`checks.json`](build/stage2d/checks.json)).
  - Videos: [`video/stage2d/`](video/stage2d/).
  - That run passed all its checks, and stage 2c was re-run afterwards (all passed).

1. **Home's bottom row.**
   - Home now fills the whole content area; the 76-px band that was kept for the date block is gone.
   - The "1–6 open a page · Ctrl K search" hint sits in a 44-px row 16 px from the bottom, the same as the date block.
     Measured: both 16 px from the bottom at 1440 and 960 px.
2. **Banners on Home.**
   - On Home, the scene is laid out once per banner change. When a banner arrives, its height comes in one step. When
     one leaves, it fades (160 ms) and then drops its height in one step. On every other page, a leaving banner folds
     its height as before.
   - The scene (wordmark, nodes, lines) is drawn at its new place, transformed so it looks like it is still at the old
     one, and glides home in 250 ms ease-out (FLIP). Only a banner triggers this; resizing the window still lays it
     out at once.
   - Measured:
     - Home's height took exactly two values per change (820 → 771 px and back), so there was no layout per frame.
     - The stage's transform took 17 in-between values, so it glides.
     - In Calm, nothing moves.
3. **Idle animation:** back to 60 fps in Full. The exception is written into MASTER §8; Calm saves the CPU.
4. **Hover cards:**
   - The node header stays on its point. The card opens towards the wordmark: top nodes open downwards, bottom nodes
     upwards.
   - It is kept 8 px inside the scene sideways. An open card is opaque and drawn above every line, so the Live line now
     ends at the card's edge instead of running across its text.
   - Measured: all six cards stay inside the scene at 1440 and 960 px.
   - The Live node, dimmed because it isn't available yet, is shown at full strength while its card is open.
5. **Accounts list ↔ editor:**
   - Opening an account or Add account: the list fades out and slides 16 px to the left (150 ms), then the editor fades
     in from 16 px to the right (220 ms, ease-out).
   - Closing, saving or discarding runs the same the other way.
   - Calm is instant.
   - Measured: 10 and 14 in-between steps; 0 in Calm.
6. **Dock:**
   - Every icon keeps a fixed 40-px slot, so nothing reflows.
   - The magnification is a `scale` transform from the icon's bottom edge, driven by a spring (stiffness 380, damping
     32), at most 1.2×.
   - Icon centres are measured once when the pointer enters the dock, not on every move.
   - Measured during a sweep: the slots never moved; the largest scale sampled was 1.18.
7. **Account dropdown:**
   - Hovering opens it and moving away closes it, as before.
   - A click on the trigger pins it open, including when it is already open from hovering. Pinned, it closes only on a
     second click on the trigger, a click outside, or Esc, wherever focus is.
   - All checked.
8. **No stale highlight:**
   - In the dropdown and the command palette, nothing is highlighted when they open.
   - A row is highlighted only while the pointer is over it or after the arrow keys moved to it; leaving the list
     clears it.
   - In the palette, Enter with nothing highlighted runs the top result (checked: "perf" + Enter opens Performance).
   - The dropdown's keyboard test now presses ↓ twice to reach the second row.

**Files:**
- Screenshots:
  - `home_live_hover_1440/960`
  - `home_bottom_row_1440/960`
  - `switcher_pinned_row_hover_1440`
  - `switcher_pinned_left_list_1440`
  - `palette_pointer_left_1440`
- Videos:
  - `home_banner_appear_leave_1440.webm`: a banner fades by itself, then another is closed with Got it.
  - `accounts_list_editor_1440.webm`
  - `dock_sweep_1440.webm`: slow, medium and fast sweeps.

## Fixes before stage 3 (2026-11-15)
- **Evidence:**
  - Screenshots and checks: [`build/stage2e/`](build/stage2e/) ([`checks.json`](build/stage2e/checks.json)).
  - Videos: [`video/stage2e/`](video/stage2e/).
  - That run passed all its checks, and stages 2c and 2d re-ran clean on the same build.

1. **Outside clicks only close.**
   - While the account dropdown or the command palette is open, a click outside it closes it and nothing underneath
     acts. Checked: a click on an account card behind either one left the page on `/accounts`, and the bell behind the
     palette did not open.
   - **The exception:** KILL and FLAT ALL act on the first click, closing the dropdown or palette at the same time.
     Checked: KILL's dialog opened with the dropdown pinned, and FLAT ALL's banner came with the palette open.
   - The palette's dimmed backdrop now starts below the 52-px top bar, so KILL and FLAT ALL stay sharp while it is
     open. Its panel stays where it was.
2. **Inert page behind.**
   - How it works: an open dropdown (hovered or pinned) or palette marks the page root (`lib/overlay.ts`). The app frame
     and the toasts then take no pointer events, enforced with `!important` on every descendant, because the dock and
     Home's nodes switch pointer events on themselves.
   - Only KILL and FLAT ALL stay live, plus the dropdown's own trigger while the dropdown is open.
   - Checked: no hover reaches the page behind. On Home, hovering a node behind the open dropdown opens no card (0), and
     after Esc the same hover opens it (1), at 1440 and 960 px.
3. **No native tooltips.**
   - Every `title` attribute in the panel became a `data-hint`. One hint layer (`components/ui/hint-layer.tsx`) shows
     it in our own styled tooltip, the dark look of originui Tooltip 21st.dev 207. It appears 450 ms after hovering, or
     on keyboard focus, below the element (above if there's no room) and inside the window.
   - The dropdown's trigger has no hint, because the dropdown itself opens on hover; its old text is now its accessible
     name.
   - Checked: no element carries a `title` on Home, Accounts, an account's editor, the new-account form, Settings,
     Performance, Review or Gameplan, at both widths.
4. **No horizontal scrollbar.**
   - The accounts list ↔ editor slide runs inside a static wrapper that clips it, and `#cf-main`, `html`, `body` and
     `#root` never scroll sideways.
   - Checked on every frame of open, close, add and discard: no horizontal overflow (0 of 41–44 frames each). No page
     in the list above overflows sideways.
5. **Home stands still.**
   - **What was found:**
     - Hovering a node moved its header: the button was centred on its point and grew with its card (measured: the
       Performance header moved 28 px left and 89 px up).
     - The headers and cards also used Motion `layout` animations, which re-measure on every re-render (the status
       stream, the clock, the next-window countdown) and animate any difference they find. That is the likely cause of
       the drift every couple of seconds.
     - The drift did not reproduce in headless Chrome at 100%, 125% or 150% scale over 12 s. Your Chrome couldn't be
       measured directly, because the browser extension isn't connected.
   - **The fix:**
     - Each node's header is a fixed element on its point. Its card is a separate overlay beside it, towards the
       wordmark, opaque, above the lines and inside the scene. Nothing on Home uses layout animation any more.
     - The scene re-lays out only when its area changes by 1 px or more; sub-pixel changes are ignored. A banner's
       glide (FLIP) is unchanged.
   - **Proof** (`home_still_idle_then_hover_1440.webm`): 30 s with every node header's centre, every line's path, the
     stage's transform and the scene's size sampled on every frame. The video is 16 s of idle, then each of the six
     nodes hovered in turn (pointer moved onto the header, then into its card). Result: **0 changes in 1,844 frames.**

## Home: animated node cards and the banner glide back (2026-11-15)
- **Evidence:**
  - Checks: [`build/stage2f/`](build/stage2f/) ([`checks.json`](build/stage2f/checks.json)).
  - Videos: [`video/stage2f/`](video/stage2f/).
  - Measurement: all positions are measured **after every painted frame** (`scripts/glide_probe.mjs`), so the numbers
    are what was on screen.
  - All checks passed, and stages 2d and 2e re-ran clean on the same build.
  - No layout animation is used anywhere on Home.

1. **Node cards.**
   - On hover a card fades in and scales up from 94% (with a 6-px drift), growing from the node towards the wordmark,
     in 180 ms ease-out. It leaves faster: 120 ms, fading to 97%.
   - While the pointer is over the open card, the card lifts 2 px and a soft cream edge glow fades in. The glow is a
     separate layer, so only its opacity changes.
   - Opacity and transform only. Calm: no motion; the card simply appears.
   - Measured at 1440 and 960 px:
     - 8–10 in-between frames on the way in, with the scale-up confirmed;
     - the card gone about 150 ms after the pointer leaves;
     - glow at full and lift −2 px over the card;
     - no node header moved;
     - in Calm, the card is fully opaque from the first frame.
2. **The banner glide.**
   - **Trigger:** only a banner arriving or leaving starts it. The banner bar records when its list changed; any other
     real size change re-lays out at once, and changes under 1 px are ignored.
   - **What jittered:**
     - The resize was handled a frame late, so the scene was painted pushed down by the banner's 49 px for about 85 ms
       (while the new layout was computed), then pulled back up.
     - The wordmark canvas was rebuilt after paint, which cleared it for a frame and re-scattered the background tape.
     - One transform for the whole scene can't carry every node. At 960 px the wordmark keeps its size (its width sets
       it) while the top and bottom nodes move apart, so they jumped 25 px.
   - **Now:**
     - In the frame the scene's box changes, the wordmark and every node (with its line) are held on screen exactly
       where they were, by transforms written straight to the elements.
     - When the new layout is placed, each one starts where it was seen and glides home once: 270 ms, an ease-out with
       a gentle start, cubic-bezier(0.4, 0.55, 0.6, 1).
     - The wordmark canvas is set up and drawn before paint, and the tape keeps its place.
     - The last 6 layouts are cached, so a banner of the same height costs nothing the second time.
     - Calm: held, then placed in one step.
   - **Measured (largest move of any node on any frame, two banners in a row):**

     | Width | Arriving | Leaving | Nodes travel | Moving frames |
     |---|---|---|---|---|
     | 1440 px | 4.8 px | 4.9 px | 5–51 px | 17–18 |
     | 960 px | 5.5 px | 5.5 px | 6–56 px | 18–19 |

     Calm moved in exactly one step each way. The first banner of a new height has about a 100-ms pause before the
     glide, while the new layout is computed; the scene holds still during it.
3. **The 30-second check again** (`home_still_cards_then_banners_1440.webm`, first 30 s): 16 s idle, then each node
   and its card hovered. Every node header's centre, line path, wordmark transform and the scene's size were sampled
   after every painted frame: **0 changes in 1,730 frames.** The same video then shows a banner arriving and fading by
   itself, and a second one closed with Got it.

## Home: no pause before the banner glide; node labels light up as one (2026-11-15)
- **Evidence:**
  - Checks: [`build/stage2g/`](build/stage2g/) ([`checks.json`](build/stage2g/checks.json)).
  - Videos: [`video/stage2g/`](video/stage2g/).
  - Measurement: everything is measured after each painted frame (`scripts/glide_probe.mjs`, `measureEvent`).
  - All checks passed, and stages 2c–2f re-ran clean on the same build.
1. **No pause, 0 frames between the banner and the nodes.**
   - **What caused the pause:**
     - Re-solving the six lines for the new node positions (`solveLines`, 70 ms).
     - Re-creating the wordmark canvas at the new height.
     - The ~100-ms raster of the first new layout (fixed in the previous round).
   - **Same start:** the scene's height now comes from the banners' target height (`onBannerTarget`). The banner bar
     reports it in the same commit that adds or removes a banner, so the glide is set up before the banner's first
     frame is painted.
   - **Banner animation on Home:** banners unfold and fold on the glide's own timing (270 ms,
     cubic-bezier(0.4, 0.55, 0.6, 1)).
   - **One clock:** banner and scene both run on the JavaScript clock. An opacity left on the compositor started one
     frame early.
   - **Ahead of time:**
     - **Layout:** the wordmark keeps the size of the banner-free layout, so each banner state's layout is that layout
       moved down (`shiftLayout`, exact, 0.3 ms). The canvas keeps the banner-free layout and is moved by the same
       offset, so it is never re-created.
     - **Lines:** solved in idle slices from load, one candidate aim at a time (`solveLinesSteps`; the same search and
       result). This covers 0–3 stacked banners, each one or two lines tall, using heights measured from two hidden
       probe banners (49 and 61 px at 960 px). It finishes in about 1.6 s, during the intro.
     - A banner of three or more lines is solved when it arrives.
   - **Measured** (the first banner right after loading, it leaving, then three stacked arriving and leaving):

     | Width | Gap in frames | Largest step | Longest frame | Lines solved late |
     |---|---|---|---|---|
     | 1440 px | 0 on all 8 events | 5.7 px | 24 ms | none |
     | 960 px | 0 on all 8 events | 5.5 px | 19 ms | none |

   - **Trade-off:** while banners show, Home's wordmark keeps its full size instead of shrinking with the scene. The
     nodes and lines still move to fit.
2. **Node labels light up as one.**
   - The label's border, background and glow, the icon and its circle, the name, the number badge (and, for the
     dimmed Live node, its strength) all change on one 180-ms ease-out transition. They fade back the same way. The
     line follows on the same timing.
   - Measured on the Gameplan label: all four parts start changing on the same painted frame and settle about 9–10
     frames later, both in and out. In Calm the change is instant.
- **Videos:**
  - `home_banners_and_labels_1440.webm`: the first banner right after loading and Got it, three stacked arriving and
    leaving, then every label hovered.
  - `home_banners_960.webm`: the same banners at 960 px.

## Home: the lines morph with the banner glide (2026-11-15)
- **Evidence:**
  - Checks: [`build/stage2h/`](build/stage2h/) ([`checks.json`](build/stage2h/checks.json)).
  - Videos: [`video/stage2h/`](video/stage2h/), `home_line_morph_1440.webm` and `home_line_morph_960.webm` (the first
    banner after loading, then three stacked arriving and leaving).
- **What popped:** each line switched to its new shape in one frame, and was only shifted rigidly with its node.
- **Now:**
  - Each line's cubic (start, two control points, end) is kept as shown on screen.
  - At a banner event, it starts from that shape and every point moves to its precomputed new shape on the nodes' own
    270-ms curve. Its `d` is written each frame.
  - The node end is the node's point, so it moves exactly with the node.
  - The wordmark end moves with the glide. The wordmark itself glides about 26 px at 1440 px, because the scene is
    centred in the space left below the banners.
  - Lines no longer get the node's rigid offset.
  - The stage and the canvas wrapper are their own compositor layers from the start (`will-change: transform`).
- **Measured** (every line sampled at 21 points after each painted frame; the first banner after loading, it leaving,
  then three stacked arriving and leaving, at 1440 and 960 px):
  - The banner, the nodes and the lines start on the same frame in all 16 events.
  - Every line changes over 16–17 frames, never in one.
  - A line's node end is never more than 0.07 px from its node.
  - The largest per-frame move of any line point equals the nodes' own: 4.3–5.6 px.
  - **Exception:** the very first banner after loading at 1440 px (7.3–9.7 px in one frame, in every run of stages 2f,
    2g and 2h).
- **That exception:**
  - That event has one late frame (26–43 ms). The Chrome trace puts the time in the GPU process: raster tasks of 25 ms
    and 36 ms. The page's scripts take under 10 ms.
  - The morph is time-based, so in that frame everything advances by the elapsed time. Scaled to a 60-Hz frame, the
    step is 4.4 px; the shapes still morph and nothing pops.
  - It happens once, only at 1440 px (not at 960), with this PC at about 76% CPU from step 13c's builds and in
    headless Chrome. Later banners stay at normal frame times. Not checked yet on the owner's own screen.

## Page switches never shift sideways (2026-11-15)
- **Evidence:**
  - Checks: [`build/stage2i/`](build/stage2i/) ([`checks.json`](build/stage2i/checks.json)).
  - Videos: [`video/stage2i/`](video/stage2i/), `page_switches_1440.webm`.
  - Measured with real 15-px scrollbars, as on Windows. Playwright hides scrollbars by default, which is why the
    earlier runs didn't show this (`scripts/page_probe.mjs`).
- **Cause 1, the scrollbar:**
  - About 240 ms into Home → Accounts, the long page's scrollbar appeared, the content area narrowed from 1440 to
    1425 px and the heading jumped 7.5 px.
  - Fix: `scrollbar-gutter: stable` on `#cf-main`. The scrollbar's space is reserved on every page all the time.
  - Side effect: Home's scene is 15 px narrower, so its centre sits 7.5 px left of the window's centre, like every
    page's content.
- **Cause 2, Settings:**
  - Settings' loading skeleton used a narrower container (1180 px, 24-px padding) than the loaded page (1280 px,
    32-px padding at 1440 px and up). Its heading jumped 42 px when the data arrived.
  - Fix: the skeleton now uses the loaded page's container.
- **Measured** (heading left edge and Home's box after every painted frame, at 1440 and 960 px; Home → Accounts and
  back, Home → Settings and back, Home → Review, Review → Accounts):
  - The content area kept one width throughout (1425 / 945 px).
  - No heading or Home edge moved at all: 0 jumps.
  - The other checks still pass. The only flags are the first-banner late frame at 1440 px from the line-morph round.

## Centred again: both-edges gutter; the line morph confirmed (2026-11-15)
- **Gutter:** `scrollbar-gutter: stable both-edges` on `#cf-main`. The scrollbar's space is reserved on both sides, so
  every page, Home included, is exactly centred in the window.
- **Measured** ([`build/stage2i/`](build/stage2i/)):
  - Home's scene and the Accounts and Settings containers sit **0.00 px** off the window's centre at 1440 and 960 px,
    with and without a scrollbar.
  - The page switches (Home → Accounts and back, Home → Settings and back, Home → Review, Review → Accounts) keep one
    content width (1410 / 930 px) and **0 jumps**.
- **Line morph, re-measured on the final build** ([`build/stage2h/`](build/stage2h/), 16 events: the first banner after
  loading arriving and leaving, then three stacked arriving and leaving, at 1440 and 960 px):
  - The banner, the nodes and the lines start on the same frame every time.
  - Every line changes over 16–17 frames.
  - A line's node end stays within 0.07 px of its node.
  - The largest per-frame move of any line point equals the nodes': 4.3–5.6 px.
  - The known exception: the very first banner after loading at 1440 px, 7.1 px in one 37-ms frame (GPU raster while
    13c loads this PC). Stages 2c, 2d, 2e, 2g and 2i pass; 2f and 2h flag only that first-banner frame.

**Stage 2 approved by the owner 2026-11-15 and committed locally (not pushed; the full suite runs after 13c).**
