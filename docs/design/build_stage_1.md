# Step 14, build stage 1: server, safety, `/emergency`, build command, app shell (2026-11-15)

The first of five stages (plan: owner 2026-11-15). Nothing is committed yet; this stops for your comments.
Screenshots and measured checks: [`build/stage1/`](build/stage1/) ([`checks.json`](build/stage1/checks.json)),
from `ui/web/scripts/shoot.mjs 1` against the running panel. **That run: all checks passed**:
- no request left 127.0.0.1;
- no console error or warning, so no CSP violation either;
- the CSP header was on every page;
- FLAT ALL stayed on one line and fully visible;
- the top bar never overflowed at 1440, 1280 or 960 px.

## How to open it
```
uv run nqbot ui build      # once per machine, and after any change under ui/web/
uv run nqbot ui            # opens http://127.0.0.1:8733/ ; Ctrl+C stops it
```
The emergency page is http://127.0.0.1:8733/emergency. `nqbot ui --no-open` starts it without opening the browser.

## What was built

### Server (`src/nqbot/ui/`, Starlette + uvicorn)
- **Dependencies:** starlette and uvicorn are now direct dependencies. Streamlit is removed from `pyproject.toml` and `uv.lock`, as decided in Q-step14a (1).
- **Loopback only (`serve.py`):** the host is fixed at 127.0.0.1, and no option changes it. The socket is bound here, its address is read back, and the server refuses to start on anything else.
- **Request checks (`security.py`):**
  - Host must be `127.0.0.1:8733` or `localhost:8733`, otherwise 421.
  - Every POST needs a matching Origin and this launch's token, otherwise 403.
  - Every response carries `Content-Security-Policy: default-src 'self'; …`, plus `nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: same-origin`.
- **The one action path (`actions.py`):** KILL, FLAT ALL, PAUSE and RESUME all go through it, from the panel, `/emergency` and the fallback page alike.
  - There is no engine before step 22, so it sends nothing and answers: "No live engine on this machine, so there is nothing to flatten or stop (the live engine and its exit path come at step 22)."
  - Every request is logged to `<root>/logs/ui_actions.jsonl`.
- **`/emergency` (`emergency.py`, `templates/`):** plain HTML forms with no script and no inline style, and a 5-second meta refresh. It shows engine state, SSD, machine and accounts, and it works with no app build and no SSD.
- **App build checks (`spa.py`):** a missing, failed or stale app build is refused, and `/` then shows the emergency page with the reason at the top.
- **`nqbot ui build` (`build.py`):**
  - `npm ci` from the committed lockfile;
  - `npm audit`, which fails on high or critical (this run: 0 advisories of any level);
  - `vite build` into the git-ignored `ui/web/dist/`, with the sources' digest recorded beside it.

### App (`ui/web/`, React 19 + Vite + Tailwind 4 + Motion)
- **Mock pieces moved in:** the Cocoa Amber tokens, the self-hosted fonts (Instrument Serif, Geist and Geist Mono only), the brand mark and wordmark, the dock with its minimize circle, and Dot Matrix Text.
- **Top bar:**
  - breadcrumb and account switcher ("No accounts yet" until stage 2);
  - engine pill: OFFLINE, or NO SERVER in red if the panel loses its server;
  - the ET clock, recomputed every second;
  - Effects: Auto, Full or Calm, showing "CALM (AUTO)" when forced;
  - the Ctrl K hint;
  - **KILL** (outlined, asks once, Enter confirms) and **FLAT ALL** (solid, one click).
- **Results of KILL / FLAT ALL** show as a banner under the tape, never a toast.
- **Lost server:** if the panel loses its server, a critical banner says kill and flat won't arrive from this page and links to `/emergency`.
- **Ticker tape:** an honest static row until the live engine exists at step 22.
- **Command palette (Ctrl K):** pages, accounts, FLAT ALL, KILL (it opens its confirm), pause, resume and the emergency page.
- **Shortcut sheet (`?`), keys and page changes:**
  - keys 1–6 open pages: Home, Gameplan, Accounts, Settings, Review, Performance (they renumber when Live exists);
  - a skip link;
  - page transitions, instant in Calm.
- **Splash:** shown on a session's first open, and kept **below** the top bar so KILL and FLAT ALL work from the first frame.
- **Safety nets in the page itself:** a `<noscript>` notice and a load watchdog (inline, allowed by its CSP hash), both linking to `/emergency`.
- **Placeholder pages:** the real page headers, with an empty state naming the stage that builds each page.

### 21st.dev components (code read for each; rows in `design-system/coverage.md` §3)
- Used and adapted to the tokens:
  - Command Palette 23522
  - Status 25395
  - Alert Dialog 702
  - InfiniteSlider (from 18221)
  - Combo Kbd 28308
  - Keyboard Shortcuts 26919
- Built in their pattern instead of installed:
  - **Sonner 27363:** its package inserts a `<style>` element at run time, which the CSP blocks.
  - **Alert 34383:** its helper files weren't returned, and its exit animation is wrong for risk banners.

### One CSP finding
Radix's scroll lock (under every dialog) and Motion's `popLayout` (the dock's minimize morph) each insert a `<style>` element at run time. The strict CSP blocked them, which the first screenshot run caught.
- **Fix:** a per-launch **style nonce**. The server adds `style-src 'self' 'nonce-…'` and puts the nonce in the page; `get-nonce` and `MotionConfig` apply it.
- No `unsafe-inline` anywhere, and a test checks this.

### One referrer finding
`Referrer-Policy: no-referrer` makes Chrome send `Origin: null` on a form post, so the Origin check refused the emergency buttons.
- **Fix:** it is now `same-origin`.
- The screenshot `emergency_after_flat_1440` shows the form working.

## Tests (targeted, rule 14 (b))
- `tests/test_ui_server.py` (14 cases):
  - Host refusal;
  - Origin and token on POST;
  - CSP on every response;
  - the style nonce with no `unsafe-inline`;
  - the token in the page;
  - `/emergency` with no app and no SSD (no `<script>`, no inline style);
  - the emergency form going through the one action path, and its log;
  - a missing, stale or failed build serving the controls with the reason;
  - static files staying inside `dist/`;
  - the status contents;
  - the loopback-only bind.
- `tests/test_ui_build.py` (5 tests): `npm ci`, never `npm install`; a high or critical advisory stopping the build; failures; the digest ignoring line endings; parsing the audit JSON.
- **Result:** the UI tests together with `tests/test_cli.py` and the fast docs/config tests: 118 passed, `PYTEST EXIT CODE: 0` (`D:\nqbot\logs\pytest_20261115-151512.txt`).
- **Full suite:** not run. It waits until 13c ends, before any push.

## Screenshots (`build/stage1/`)
| Shot | Shows |
|---|---|
| `accounts_1440`, `_1280`, `_960`; `performance_*` | the shell at three widths: the dock, and the top bar narrowing as it should (breadcrumb, then the Effects label, then Ctrl K) |
| `flat_result_1440`, `_960` | FLAT ALL pressed: a warn banner saying nothing was sent and why |
| `kill_confirm_1440` | the KILL dialog above everything, with Enter to confirm |
| `palette_1440`, `shortcuts_1440` | Ctrl K with "fla" typed; the `?` sheet |
| `dock_minimized_960`, `dock_hover_1440` | the corner circle; magnification and tooltip |
| `calm_1440` | the OS asking for reduced motion: Effects shows CALM (AUTO) |
| `splash_1440` | the first open of a session: the splash below a working top bar |
| `emergency_1440`, `_960`, `emergency_after_flat_1440` | the no-JavaScript page before and after a FLATTEN ALL |
| `fallback_missing_build_1440` | `/` with no app build: the reason and the controls |
| `noscript_1440` | JavaScript off: the red notice linking to `/emergency` |

## Notes and choices for you
- **KILL sits next to FLAT ALL in the top bar.**
  - MASTER §10 names one control; I gave KILL its own outlined button so it is never one menu deeper.
  - It is the only control with a confirm, and Enter confirms.
  - PAUSE / RESUME are in Ctrl K and on `/emergency`; the Live page gets them at step 21.
- **The Effects setting** is remembered in the browser for now. Stage 2 moves it to `data/panel/ui.yaml` with the other panel settings.
- **`nqbot ui` holds Windows awake while it runs**, like every `nqbot` command.

## Next: stage 2 (Home, Accounts, Settings)
- the Home scene, with the node numbers;
- the account schema and the §6.7 presets;
- the draft accounts `mffur1` and `mffub1`;
- the edit form with its live summary and validation;
- Save as preset;
- the raising-risk dialog;
- the Settings page;
- `nqbot ui backup`;
- the panel files in `<data_dir>/panel/`.

## Your stage-1 changes (2026-11-15)
Screenshots and measured checks: [`build/stage1b/`](build/stage1b/) ([`checks.json`](build/stage1b/checks.json)),
from `ui/web/scripts/shoot.mjs 1b`. **That run: all checks passed.** The only two load failures are the flat requests
the script aborts on purpose to raise a critical banner.

1. **KILL dialog, redesigned.**
   - **Component:** searched 21st.dev and picked Alert Dialog Layout 28277 (ai2), its split layout: a crit-tinted band
     with the icon and the title, then what KILL does, line by line.
   - **It replaces** the Radix Alert Dialog; the `@radix-ui/react-alert-dialog` package is removed.
   - **Behaviour, checked:** focus is on KILL when it opens, so Enter confirms at once. Esc, Cancel or a click
     outside backs out. Keycap hints on both buttons; never hold-to-confirm; it appears at once.
   - **Correction:** the old text said KILL "stops the engine". SPEC §9c says `!kill` sends `exit` to every account
     and ticker, then blocks new entries until the next session, and the engine keeps running so the exits can
     retry. The dialog, the button tooltips and `/emergency` now say exactly that, and the FLATTEN ALL / PAUSE /
     RESUME lines follow §9c too. A test pins the `/emergency` wording.
   - **Note line:** while no engine runs here, the dialog says pressing KILL sends nothing yet.
   - **Shots:** `kill_dialog_1440`, `kill_dialog_960`.
2. **Banners.**
   - **Motion:** they slide down with a short fade (220 ms).
   - **Info banners** (a "nothing was sent" note, a successful flat) fade out after about 5 s. The countdown pauses
     while the pointer is over them, and Got it closes them at once.
   - **Warnings and critical banners** never fade by themselves.
   - **Measured:** an info banner was gone after 5.6 s, still there after 6.5 s while hovered, and gone again 5.6 s
     after the pointer left. A critical banner was still there after 7 s.
   - **One exception to the slide:** critical banners appear at once, without the slide. MASTER §7 says risk alerts
     never animate in. Say the word if you want them to slide too.
   - **Bell (new):** before KILL in the top bar. It keeps the history of every banner and toast shown in this tab,
     newest first, with tabs All / Alerts / Info and the ET time of each. The unread count turns red while a
     critical one is unread.
   - **Bell panel:** it opens *below* the banner stack. The first try opened it under a critical banner, which hid
     the panel's header; a banner is now never covered.
   - **Shots:** `banner_info_1440`, `banner_info_960`, `banner_crit_1440`, `bell_open_1440`, `bell_open_960`.
3. **Date block.** The serif day number with "NOV 2026 / SUN · EASTERN" sits in the bottom-left corner of every page,
   Home too.
   - **Look:** a dark chip like the dock's glass, so it stays readable over the footer letters later.
   - **Date:** New York's date, recomputed from the clock.
   - **Room:** every page keeps at least 76 px of bottom room for it.
   - **Clearance:** it is 83 px clear of the open dock at 960 px, 243 at 1280 and 323 at 1440, and never touches
     the minimized circle in the right corner.
   - **Narrow windows:** hidden below 900 px.
   - **No second spot proposed:** the dock already reserves that bottom band on every page, so the corner costs no
     content space. Inside the dock's left end it would vanish on Home and with the dock minimized.
   - **Home:** the scene in stage 2 keeps that corner clear.
   - **Shots:** `date_block_home_1440`, `date_block_home_960`, `date_block_page_960`.
4. **Command palette.**
   - **Focus:** the search field has no outline any more, only a faint line under it. The global focus rule had won
     over the field's own class; it is now overridden.
   - **Position:** the palette sits slightly above the vertical centre (its middle at 45% of the window height) and
     its top stays put while typing. Measured: top 224 px before and after typing at 1440 × 900, 178 px at 960 × 800.
   - **Shots:** `palette_1440`, `palette_960`.

Tests: `tests/test_ui_server.py` and `tests/test_ui_build.py`: 19 passed, `PYTEST EXIT CODE: 0`.

## Your second round of stage-1 changes (2026-11-15)
Screenshots and checks: [`build/stage1c/`](build/stage1c/) ([`checks.json`](build/stage1c/checks.json)), from
`ui/web/scripts/shoot.mjs 1c`; the video is listed in `docs/design/videos.md`. **That run: all checks passed.**

1. **KILL and FLAT ALL as their own group.** They now sit 20 px after the bell; every other item in the top bar is
   10 px from the next. Shots: `topbar_1440`, `topbar_960`.
2. **KILL dialog: three variants to pick from.** Open one with `?kill=ledger`, `?kill=band` or `?kill=seal` (ledger
   is the default until you pick).
   - **What all three share:**
     - an Instrument Serif title "Kill trading *for this session?*", with the second half in cream italic;
     - a red-tinted icon badge, a thin red edge and a soft red glow on the card, and a darker, more blurred backdrop;
     - Esc and Enter key chips on the buttons;
     - what KILL does as a short list with icons and live counts.
   - **Counts:** accounts sent `exit`, open positions closed, pending entries removed. They show "—" until the engine
     exists (step 22), and the note says so.
   - **Behaviour, checked in all six shots:** focus on KILL, Enter confirms, Esc or Cancel backs out.
   - **Ledger** (`kill_ledger_1440`, `_960`): the badge and title on the left, then a ruled list with each count on
     the right.
   - **Band** (`kill_band_1440`, `_960`): the 28277 split layout made richer: a red gradient band with a solid red
     badge, the list with count chips.
   - **Seal** (`kill_seal_1440`, `_960`): centred, with a large glowing badge and the counts as three tiles. The two
     rules are lines under the tiles, and the buttons are full width.
   - **One line I didn't use:** "engine stops, manual restart needed". SPEC §9c says KILL keeps the engine running
     (exits retry, status KILLED) and blocks entries until the next session, so every variant says that.
   - **MASTER note:** the rule "no glow on the kill/flat control" is about the top-bar buttons, which stay plain. The
     glow is on the confirm card only, as you asked.
3. **Command palette:** a click anywhere outside it now closes it, as Esc does.
   - **The cause:** the backdrop and the full-width wrapper beside the palette swallowed the click.
   - **Checked:** at 1440 and 960 px, clicks left of it, right of it and below it close it; a click inside keeps it
     open.
   - **Shots:** `palette_1440`, `palette_960`.
4. **Banners fold away.**
   - **How:** a banner leaving (by fading after ~5 s, with Got it, or with Acknowledge) first fades (160 ms), then its
     height folds (260 ms, ease-out), so the page below and any banners under it glide up.
   - **Measured:** the page heading moved 181 → 177 → 156 → 144 → 137 → 133 → 132 px over the fold, instead of
     jumping.
   - **Calm:** the banner is gone within 60 ms.
   - **MASTER note:** MASTER §7 animates only transform and opacity, so this one height animation is an exception,
     made at your request.
   - **Video:** `docs/design/video/stage1c/banners_appear_fade_1440.webm`.

## Final round (2026-11-15): the band dialog and its behaviour
Checks: [`build/stage1d/checks.json`](build/stage1d/checks.json); video
`docs/design/video/stage1d/kill_dialog_behaviour_1440.webm`. **That run: all checks passed.**

- **Band variant kept.** Ledger and seal are deleted from the code, and so is the `?kill=` switch. Their screenshots
  stay in `build/stage1c/` for the record.
  - **Position:** the card's centre sits 26 px above the middle at 1440 × 900 and at 960 × 800 (`kill_dialog_1440`,
    `kill_dialog_960`).
- **Esc closes it wherever focus is.** It now listens at the window level, in the capture phase.
  - **A click on the card's text** no longer moves focus: it stays on KILL.
  - **Tab** cycles Cancel and KILL only.
  - **Any focus that escapes** comes straight back.
  - **Checked:** after a click on the text, focus was still on KILL and Esc closed the dialog.
- **A click outside does nothing.** Only Cancel, Esc or KILL close it. Checked with three clicks outside: still open,
  focus still on KILL, then Esc closed it.
- **No focus ring after mouse use.** The panel tracks the last kind of input and hides focus rings while it was the
  mouse.
  - **Checked:** Cancel with the mouse returns focus to the top-bar KILL with no ring; Shift+Tab then Tab brings the
    ring back.
- **Docs:** SPEC §9b, §6.7 and §13 Q-step14a (16) record the stage-1 decisions (the panel file on the SSD, the KILL
  wording and dialog, banners, the bell, the date block, the palette, focus rings, the style nonce); CLAUDE.md
  Commands and Status are updated.
