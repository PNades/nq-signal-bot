# Step 14a part 0, step 4: final round (2026-11-15)

Your picks from [round 2](step4_round2.md), built in the mock (`docs/design/mock/`) and recorded in
[`design-system/picks.md`](../../design-system/picks.md). Nothing is committed.

- **Screenshots and checks:** [`step4/final/`](step4/final/), with every measured value in
  [`checks.json`](step4/final/checks.json), from `docs/design/mock/shot-final.mjs`.
- **That run:** no blocked requests, no console errors or warnings, all checks passed.
- **Preview switches:** `?view=performance&account=mffur1|lucid1|all&tab=drawdown|montecarlo`, `&dockmin=centre`
  for the other dock variant.

## What changed

### Dock
- **Minimized circle:** goes to the bottom-right corner by default now
  ([dock_min_corner_over_letters_1440](step4/final/dock_min_corner_over_letters_1440.png)).
- **Darker glass:** the shell is now 90% `#111111` with a backdrop blur (it was 70% `bg-2`).
  - Worst case, with pure cream behind it, the glass reads as about `#292623`.
  - The page icons sit on opaque `bg-3` circles, so they read the same over the letters as anywhere else.
  - The minimized circle's cream icon has about 12:1 contrast.
  - Screenshots: [dock_over_letters_1440](step4/final/dock_over_letters_1440.png),
    [dock_over_letters_960](step4/final/dock_over_letters_960.png).

### Showpieces
- **All kept**, listed with their rules in `picks.md`.
- **Border beam:** on one element per page, now a rule in MASTER. Counted on each page: style tile 1, Performance 1,
  Home 0.
- **Dot Matrix Text stops redrawing once settled.** Its loop now ends when every dot has reached its target, keeping
  the last frame on the canvas, and starts again only for a text change or a resize.
  - The check logged its state for 9 s: settled at 0 s, woke at 3.3 s for the next text, settled at 3.9 s, woke at
    6.8 s, settled at 7.4 s.
  - So it draws for about 0.6 s per 3.5-s cycle and is idle otherwise.

### Performance KPI tiles
- **Order** (checked on the page): Net P&L, Trades, **Net R per trade**, Win rate, Profit factor, Max drawdown.
- **Net R per trade** is the net R ÷ the trade count, shown as e.g. `+0.80R`. Its sparkline is the monthly average,
  and its change compares March with the Oct–Feb average, like the others
  ([performance_all_1440](step4/final/performance_all_1440.png)).
- **Account filter:** All accounts or one account, each with its preset and phase as a sub-label
  ([account_menu_1440](step4/final/account_menu_1440.png)). Every view follows it: KPIs, equity, calendar and trades
  ([performance_lucid1_1440](step4/final/performance_lucid1_1440.png)).

### Drawdown and Monte Carlo follow each account's real rule
**The rule** comes from the same fields as `config/accounts.yaml` (SPEC §6.7), implemented in `floorPath`
(`src/perf/data.ts`):
- static: floor = start − drawdown;
- EOD-trailing: floor = end-of-day high − drawdown, with its lock:
  - `floor`: it stops rising at the lock value;
  - `first_payout` (Pro 50k funded): trailing until the first payout, then fixed at the lock;
  - `floor_or_first_payout` (Lucid Flex funded): locked at $25,100 when the floor gets there or at the first payout,
    whichever comes first;
- a breach = an end-of-day balance at or below the floor in force.

**Checked by hand on five cases:**

| Case | Result |
|---|---|
| Static | breach at $23,999 against a $24,000 floor |
| Trailing | floor $25,500 after a $26,500 high; $25,400 breaches |
| Lucid, lock by the floor | an EOD high of $26,300 locks the floor at $25,100; $25,100 breaches |
| Lucid, payout first | a payout before the floor gets there locks it at $25,100 |
| Pro, first payout | floor $51,000 before the payout, then $50,100 even though trailing would be higher |

**The two mock accounts:**
- mffur1 = MFFU Rapid EOD 25k evaluation ($1,000 EOD-trailing, no lock);
- lucid1 = Lucid Flex 25k funded ($1,000 EOD-trailing, Lucid's lock);
- both risk $333 a trade with their own commissions.

**Drawdown chart:**
- **Floor line:** shows the drawdown from the end-of-day high, with the floor drawn the same way (floor − high) as a
  stepped dashed line. A breach is the drawdown reaching it.
  - mffur1's line stays at −$1,000.
  - lucid1's locks on Oct 15 at $25,100 (badge). After that it steps down each time the high rises, then leaves the
    chart: the room grows.
- **Tooltip:** drawdown, balance, floor and the room to it. Example from lucid1 on 2026-01-20: −$340.00, $32,679.22,
  floor $25,100.00, room $7,579.22.
- **Screenshots:** [drawdown_all_1440](step4/final/drawdown_all_1440.png) (one panel per account),
  [drawdown_lucid1_1440](step4/final/drawdown_lucid1_1440.png),
  [drawdown_mffur1_1440](step4/final/drawdown_mffur1_1440.png),
  [drawdown_all_960](step4/final/drawdown_all_960.png) (stacked).

**Monte Carlo:**
- 1,000 reshuffles of each account's whole days, every path through its own rule, so its floor moves with that path's
  high and lock.
- Chance of reaching the floor: mffur1 **78.1%** (median trading day 43), lucid1 **43.5%** (median day 11; Lucid's lock
  protects the paths that get past it).
- **Screenshots:** [montecarlo_all_1440](step4/final/montecarlo_all_1440.png),
  [montecarlo_lucid1_1440](step4/final/montecarlo_lucid1_1440.png),
  [montecarlo_all_960](step4/final/montecarlo_all_960.png).

**Mock limits:**
- The mock has end-of-day balances only; the step-15 prop simulator also checks intraday equity.
- It models no payouts, so the `first_payout` triggers are covered by the rule function and its checks above, not in
  the charts.
- **Mock seed:** I changed it (seed 267) so that neither account breaches in the backtest order and the Lucid floor
  locks. With $333 risk on a $1,000 trailing drawdown (3 lives), only 1 of 400 seeds tried does that. The Monte Carlo
  still shows the real risk. All numbers stay MOCK DATA.

## Coverage check

- **Method:** a fresh agent compared `design-system/coverage.md` with every element in `design-system/pages/*.md` and
  MASTER §10–11, plus this round's additions: 129 elements checked, **27 had no row**.
- **Where the gaps were:**
  - most belong to pages not mocked yet: Live had no section-1 rows at all, plus the main chart and several shared
    states (error, stale, inline notice, base buttons);
  - four are from this round: the dock minimize circle, the account filter, the floor-locked badge and Home's opening
    sequence.
- **Fix:**
  - **Searched:** 12 searches on 21st.dev for the gaps that needed a component.
  - **New rows:** every gap now has a row, marked (2026-11-15), with candidates or a plain "our own, no component
    needed" and why.
  - **Picked rows:** the 16 rows with a pick now say **picked**.
  - **Contradictions fixed:** two section-2 rows that the picks contradicted are corrected (Docks was "no place",
    Borders was "no place").
  - **Gameplan drop zone:** it accepted `.md,.txt`, while SPEC §3.4 and §9b say `.md`, `.pdf` or `.docx`. Corrected.
  - **Not read yet:** the candidates found this round have not had their code read; that happens when one is built,
    as the file's rule says.

## Docs updated

- `design-system/picks.md` (new)
- `coverage.md`
- `pages/shell.md` (corner, dark glass)
- `pages/performance.md` (KPI set and order, account filter, the drawdown and Monte Carlo rules)
- `MASTER.md` (one border beam per page, canvas showpieces stop once settled)
- SPEC §13 Q-step14a (12) (decisions only)
- the CLAUDE.md Status line for 14a (one line)

The fast docs tests passed (`PYTEST EXIT CODE: 0`).

## Note

When I first checked the data module, `npx esbuild` downloaded esbuild 0.28.2 into npm's cache (not the project), an
unpinned download I hadn't meant to make. It changed nothing in the repo or the mock's lockfile. I switched to Node's
built-in TypeScript support for the checks.

## Still open

- **The step-4 videos predate this round.** The Performance video shows the old KPI row and the fixed $2,500 limit;
  the screenshots above are current. I can re-record them in the wrap-up.
- **The two inline-style CSP notes** (Waterline is no longer used; the border beam still injects one) move into the
  bundled CSS at the build.

## Round 4 (2026-11-15, after the wrap-up commit 34d0f79)

Your two follow-ups. Screenshots and measurements are in [`step4/round4/`](step4/round4/) (`checks.json`), from
`docs/design/mock/shot-round4.mjs`. No blocked requests, no console errors or warnings, all checks passed.

1. **Home messages.**
   - **Where:** every toast and the key hint now sit in one row in the bottom-right corner, 14 px from the bottom edge
     ([home_toast_1440x900](step4/round4/home_toast_1440x900.png)). Before, they sat bottom-centre behind Settings.
   - **No overlap:** checked against every node at 1920 × 1080, 1440 × 900, 1280 × 800, 1024 × 768, 960 × 1000 and
     1440 × 760, with the Performance node's card hovered first.
   - **Why one row:** at 1440 × 900 the Performance node and its open card reach x 1293 and y 871. That leaves no
     room for a stacked toast beside it, so the toast and hint share one row along the bottom edge.
   - **Keys close cards:** a key that opens a page also closes any open hover card, so a message never appears under
     one.
   - **Performance opens:** in the mock, clicking Performance or pressing 6 opens the Performance page (checked:
     the title becomes "CreamFlow · Performance"). The other nodes keep "Opening <page>…".
2. **Toasts never under the dock**, whichever variant.
   - **How:** the shell sets one CSS variable, `--toast-bottom`, to the dock's footprint plus 16 px: 96 px open,
     76 px minimized. Toasts sit bottom-right at that height.
   - **Measured** with the Export CSV toast: dock open, gap 14 px above the dock; minimized to the corner, 16 px above
     the circle ([toast_corner_960](step4/round4/toast_corner_960.png)); minimized at the centre, 16 px. Checked at
     1440 and 960 px.
3. **Footer:** a third of the letter height is now hidden by the bottom edge, at every width (was a quarter).
   - **Measured share hidden:** 1920 0.342, 1440 0.346, 1280 0.320, 1024 0.312, 960 0.329 (the dot grid rounds by a
     few pixels).
   - **The page still ends inside the letters:** 0 px below the footer
     ([footer_1440](step4/round4/footer_1440.png)).

**Docs:** `MASTER.md` (toasts), `pages/home.md`, `pages/shell.md`, `pages/performance.md`, `picks.md`, SPEC §13
Q-step14a (13).

**Footer follow-up (owner 2026-11-15):** the bottom edge now hides 2/7 (≈ 29%) of the letter height, hidden to visible
2:5 (it was a third). Measured hidden share: 1920 0.294, 1440 0.298, 1280 0.270, 1024 0.267, 960 0.281. Still 0 px
below the footer. Screenshots: `step4/round4/footer_<width>.png` (re-shot). Nothing else changed.

**One account at a time, and an MFFU Builder account (owner 2026-11-15):**
- **Switcher:** the Drawdown and Monte Carlo tabs show one account at a time. (The arrows-and-segmented switcher
  first built here was replaced the same day by the tab-row switcher below.)
- **Following the filter:** when the page's account filter is one account, the switcher follows it; moving the
  switcher then moves the filter, so the KPIs and the chart always show the same account.
- **Builder rules:** the repo already had them in SPEC §6.7's preset table (`config/accounts.yaml` holds no accounts
  yet and points there). The mock's mffub1 is a Builder 25k funded account: $1,000 EOD-trailing until the floor
  reaches $25,100 (start + $100, with the $1,100 buffer), then static; commissions $4.68 / $1.90; its $600 soft DLL
  and payouts are not modelled in the mock.
- **Seed:** 267 still works with three accounts. No account breaches in the backtest order, and mffub1's floor locks
  on Oct 2, lucid1's on Nov 17.
- **Monte Carlo chance of reaching the floor:** mffur1 54.5%, mffub1 25.2%, lucid1 59.4%.
- **Checks and screenshots:** see the tab-row switcher below (the same script and folder).

**The account switcher moves into the page-tab row (owner 2026-11-15):**
- **The Waterline trial is dropped:** the liquid-glass bar as the switcher left too much empty space above the chart.
  Its changes to the shared navbar component were reverted, and nothing of it is committed.
- **Where:** on Drawdown and Monte Carlo the switcher now sits in the page-tab row, centred in it, after a faint
  divider. It uses the same underline tabs at the same size, one per account with its name and a small type badge
  (`mffur1` Rapid, `mffub1` Builder, `lucid1` Lucid Flex).
- **Animation:** it fades and slides in when Drawdown or Monte Carlo opens, stays in place without re-animating
  between those two (checked: the same element, fully shown at once), and fades out on any other tab.
- **Keys and filter:** ← → switch accounts and wrap. It follows the page's account filter and moves it.
- **Compact fallback:** when the row can't hold the page tabs, the account tabs centred and the filter chips, it
  becomes a compact dropdown. The dropdown sits in the space between the page tabs and the chips, centred there,
  because a truly centred dropdown would overlap the page tabs by 43 px at 960. Resizing back up returns to tabs.
  - At 1440 the tabs need 437 px of the 466 available and fit.
  - At 960 the dropdown shows, 22 px clear of the page tabs and of the chips.
- **Checks:** `docs/design/mock/shot-switcher.mjs`, [`step4/switcher/checks.json`](step4/switcher/checks.json); all
  passed, no console errors.
- **Screenshots:** [drawdown_1440](step4/switcher/drawdown_1440.png),
  [drawdown_1440_mffub1](step4/switcher/drawdown_1440_mffub1.png), [montecarlo_1440](step4/switcher/montecarlo_1440.png),
  [drawdown_960](step4/switcher/drawdown_960.png), [drawdown_960_menu_open](step4/switcher/drawdown_960_menu_open.png).
