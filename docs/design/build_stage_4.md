# Step 14, build stage 4: the main chart, the GEX radial, the Review page (2026-11-16)

The fourth of five stages (plan: owner 2026-11-15). Nothing is committed yet; this stops for your comments.

**Evidence** (`ui/web/scripts/shoot.mjs`):
- [`build/stage4/`](build/stage4/) ([`checks.json`](build/stage4/checks.json)): the Review page on the real preview round
  at 1440, 1280 and 960 px (read-only: nothing answered).
- [`build/stage4s/`](build/stage4s/) ([`checks.json`](build/stage4s/checks.json)): a `--synthetic` panel, for answering,
  the reveal, the result card, the tooltips, ES, Table, Focus off and Calm.
- **Both runs: all checks passed.**
  - No request left 127.0.0.1, and there was no console error.
  - FLAT ALL stayed on one line, and no native tooltip appeared.
  - Nothing scrolls sideways.
  - The replay bar never sits under the open dock.
- The shots ran against two scratch panels: 127.0.0.1:8734 (your SSD, read-only) and 8735 (synthetic, its answers in
  memory).

## How to look at it
```
uv run nqbot ui build
uv run nqbot ui              # Review is key 5 / the dock's eye icon
```
- **Restart the panel already running on 8733 first.** It is a `--synthetic` panel from an earlier session, on the old
  server code, which has no Review routes.
- **The preview round `preview-b958`** is on the SSD: 60 cards from build b958fe6e42aa (labels 8e9cdad94970, months
  1–4). Answering a card there writes `D:\nqbot\data\reviews\answers_preview-b958.parquet`, a preview file only.
- **Your real round** (50–100 cards) is drawn from 13d's months 1–6 build once it exists (Q-step14a (16)):
  `uv run nqbot review round-new --build-key <key> --labels-key <key>`. `uv run nqbot review rounds` lists the rounds.

## What was built

### The data: a card is cut at its decision time (§8)
- **`src/nqbot/review/sampler.py`:** the §8 step-14 sample.
  - **One candidate per event:** style (c) if an audit or sweep fired (its first firing), else (d).
  - **The trade:** the bootstrap pair (stop candidate 2, the nearest target at 1:1 or more; the 13c policy's own
    `pick_bootstrap`), at the style's order type, and only one whose entry filled.
  - **The round:** 20% from setups that lost and 10% "never take" (open question below); the rest spread across
    stack count, level group, entry style and time of day.
  - **Limits:** one card per touch group and at most 3 per session.
  - **Order:** seeded and shuffled, so the order says nothing about the outcome.
  - **Preview round coverage:**
    - stack count 1 / 2 / 3+: 20 / 20 / 20;
    - level groups: golden zone, VWAP, highs/lows, nodes, profile, expected move, 10 each;
    - entry style (c) / (d): 30 / 30;
    - time of day: 14–16 cards per bucket;
    - strata: 12 lost and 6 never-take.
- **`src/nqbot/review/cards.py`:** one card's data, read from the stored build, the stored trades and the stored Skylit
  boards, through the bot's own code (`features/boards.py` for the applied boards and the R in force,
  `features/node_switch.py` for the confirmed King).
  - **`pre`** (what was known at T):
    - the NQ and ES 1-minute bars, the bar in progress built from the trades up to T only;
    - every level's band per minute, its role over the day and its cross / flip / reclaim instants
      (`review/roles.py`);
    - the zones as they stood at each decision point;
    - the earlier touches, with a label only once it was known;
    - the four maps once a minute over the replay span (the 40 largest strikes at 5% or more of the largest);
    - the confirmed King's switches.
  - **`post`:** the same from T's minute to the exit (or the structure label) + 15 min, plus the outcome: the result,
    net R, fill and exit, worst and best excursion, and the structure label.
  - **A finding fixed on the way:** style-(c) decisions fire exactly at a bar close (12:05:00.000), so "T's minute"
    has no trade yet. The card's last bar is now the 12:04 bar unless a trade is stamped exactly at T.
  - **Pruning:** levels and zones far from the prices shown so far (± 1%) are left out, from the bars up to T only, so
    the pruning hints at nothing later.
  - **Size:** 600–860 KB a card. A round of 60 takes about 6 minutes to draw (about 7 s a session, mostly reading the
    boards).
- **`src/nqbot/review/store.py`:** rounds and answers.
  - **Development months only:** refused when the round is drawn, and checked again on every card read (§7 rule 2b).
  - **Answers:** `answers_<round>.parquet`, written under a lock, one row per card, final once the outcome was shown
    (a second answer gets 409).
  - **The decision-time cut:** `public_card` sends no `post`, no outcome and no stratum before the answer. The
    stratum would give "lost" away, so it stays hidden too.
- **`src/nqbot/ui/api_review.py`:**
  - `GET /api/review` and `GET /api/review/card`.
  - `POST /api/review/answer`, which needs the token.
  - **`--synthetic`:** hand-built cards, with answers kept in memory and never written.
- **`src/nqbot/review/synthetic.py`:** the hand-built synthetic day and cards (Q-dryrun13 (12)), for the tests and
  `nqbot ui --synthetic`. A card you flag later can be reproduced with it as a regression test (rule 11).
- **CLI:** `nqbot review round-new | rounds`.

### The main chart (`ui/web/src/chart/`, shared: Live and Gameplan reuse it later)
- **Candles:** cream-white up and periwinkle down, on Lightweight Charts. One overlay primitive draws the rest
  (`overlay.ts`):
  - **zones:** a band when two or more levels sit in them, hatched while provisional, coloured by role;
  - **single levels:** thin lines, dashed while provisional, coloured by the current role, with ◇ cross, ◆ flip and
    ○ reclaim markers;
  - **touches:** ● held, ✕ broke, ◌ timeout, and the card's own touch as a cream ◆;
  - **King switches:** crowns;
  - **orbs:** the relevant nodes at the replay clock, Pika gold / Barney orchid, the King the brightest with its
    ring;
  - **the proposed trade:** entry, stop and target from the decision on, with their exact prices on the price axis;
    after the answer, the fill ▲ and the exit.
- **The right margin:** after the last bar there is an orbs column, then one label column. The trade's labels come
  first; level labels that would collide are skipped.
- **Focus (on by default):** the card's zone, the levels stacked in it and the target's level are at full strength;
  every other level and zone is faint context. The first version drew every zone and level at full strength, and the
  chart was unreadable. Turning Focus off shows all equally (`focus_off_1440`).
- **Toolbar:**
  - NQ | ES (ES shows its bars and the SPY / SPX nodes);
  - layer toggles: Zones, Lines, Highs / lows, Nodes, Touches;
  - Focus;
  - Table, the same numbers as a table (MASTER §11);
  - full screen (F).
- **Crosshair tooltip:** the bar's OHLC; over a level, its role timeline so far (§9b: "ceiling from 08:00 · crossed
  11:20 → floor (provisional) · flipped 11:25"); over an orb, the node's value, share of the King and chart price.
- **No native tooltips:** Lightweight Charts' attribution link (kept, its licence) carried a native `title`; it is now
  our hint.

### The GEX radial (`GexRadial.tsx`, canvas)
- **A dial:**
  - strikes run low → high around a 240° arc;
  - each node is a spoke out to its share of the largest node, on a square-root scale, so a dominant King doesn't
    flatten the rest;
  - rings at 25% (the relevance line), 50% and 100%;
  - spot is the needle, with "≈ NQ 21,374" under it;
  - Pika gold / Barney orchid with + / − in every label;
  - the King is the brightest, with its ring;
  - a node that is a level at the clock is drawn full, the others faint.
- **Maps:** QQQ, NDXP, SPY, SPX.
- **Motion:** it follows the replay clock. Full: the spokes glide (400 ms); Calm: one step.
- **Hover:** strike, value, share and chart price.
- **Why a dial:** I first drew the shortlist's "rings by distance from spot". Evenly spaced strikes made a spiral, with
  labels on the centre.

### The Review page
- **Header:**
  - the flip-disk counter ("07/60") with "n agreed with the outcome";
  - the round chip, with the build and labels in its hint;
  - a one-row card strip (a dot shows agreed or against once answered);
  - Back (P), Later (L: the next card not answered) and Next card (N).
- **Main chart** with the **replay bar**:
  - Restart (Home), a minute back and forward (← / →), Play (Space), 1× / 2× / 4×;
  - the slider from T − 45 min (not before 09:30) to T;
  - the clock, and "decision 10:39:00 · the chart stops here".
  - **The plot's height:** what's left above the dock (or the minimized circle), between 260 and 760 px, so the
    replay bar is never under the dock.
- **"At this point"** (right):
  - setup, zone, what is stacked in it at T, entry style, price;
  - the proposed trade: entry with its order type, stop and target with their points, reward : risk net of costs.
- **Your answer** (under it, clear of the dock):
  - Take (T) / Skip (S);
  - Why, required;
  - Optional: how you'd enter (order-flow trigger / audit / plain), your stop and target, confidence 1–5;
  - "Answer and reveal" (Ctrl+Enter).
  - **Drafts:** kept per card in this browser, so leaving a card or the page keeps them.
- **After the answer:**
  - the result card drops in with a spring (Full) or at once (Calm). It shows Take profit / Stop loss / Timeout, net
    R with sign and arrow, "Agreed with / Against the outcome", what you said, the structure label, the worst / best
    excursion, the stratum and your reason;
  - the rest of the path plays out on the chart (Full, about 90 ms a minute) or appears at once (Calm).
- **Below:** the GEX radial and the detector readings at the decision:
  - confluence, nodes, regime, freshness and the day so far;
  - P(trend) "from step 15", the order-flow trigger "from step 17".
  - The readings come from the scorer's own inputs at that decision point: `decision_rows` for (c), the touch row
    for (d).

## Checks (measured)
- **The decision-time cut, as served:** for each synthetic card before its answer, the API's card had no
  `post` / `outcome` / stratum, no bar, level segment or map snapshot after T, and no label on its own touch.
- **Keys:** T picks Take (`aria-checked` true), the reason box takes focus, Ctrl+Enter answers.
- **The result card:** in place 200–240 ms after Ctrl+Enter (70 ms in Calm).
- **The reveal:** played on to 10:52, 12:05 and 13:25 ET; in Calm it was there at once.
- **Replay:** Restart then → × 3 gave 09:33 ET for a 10:12 card (its replay starts at 09:30).
- **Tooltips:** the level tooltip read "ON high · ceiling from 08:00"; the orb tooltip showed the King.
- **The dock:** the replay bar is clear of the open dock at 1440 × 900, 1280 × 800 and 960 × 800.

## Tests (targeted, rule 14 (b))
- **`tests/test_review_roles.py`:** cross / flip / cross back / reclaim / resume, same-instant events, roles set from
  price's side, unknown names logged.
- **`tests/test_review_sampler.py`:**
  - (c) before (d) and tradeable only;
  - the quotas, one per touch group, the per-day cap;
  - every stratum value covered, repeatable by seed;
  - the never-take fallback.
- **`tests/test_review_cards.py`:**
  - nothing in `pre` after T, and the bar in progress built from the trades up to T only;
  - the outcome and later labels hidden;
  - roles and markers, maps, King switches, zone members;
  - the board leader equal to `node_switch.leader` on 300 random boards;
  - the bar-close decision minute.
- **`tests/test_review_store.py`:**
  - a test month refused when drawn and on every read;
  - answers stored once with the outcome, and validated;
  - the API sends only `pre` until the answer, and refuses a POST without the token;
  - no SSD / no round explained;
  - synthetic answers never written;
  - bad ids refused.
- **Result:** with the UI tests, `test_cli` and the fast docs/config tests, 157 passed, `PYTEST EXIT CODE: 0`
  (`D:\nqbot\logs\pytest_20261116-182344.txt`). The full suite waits for 13c (no push).

## Open questions for you (SPEC §13 Q-review-sample)
1. **"Setups you'd never take"** (§8) are drawn, for now, from single-level zones traded against the day's direction
   so far, else any single-level zone. Is that the kind you meant, or do you have a definition?
2. **The round:** 60 cards by default, at most 3 a session, 20% that lost, 10% never-take, only filled entries. Keep?
3. **Level colours:** every level line is coloured by its role, as §9b says. MASTER §3.6 lists VWAP / POC / value-area
   lines as neutral grey; I use grey only before a level has a role.
4. **The radial as a dial** (above), instead of rings by distance.

## 21st.dev
No new component was installed in this stage. All of these are our own:
- the chart overlay, the dial, the replay bar, the answer panel and the result card;
- the flip-disk counter (24220) and the key chips (28308), reused from the mock and stage 1.

The coverage rows for Review (replay scrubber 35685 / 30231, rating group 8241, presence-fade 18251, swipe deck 23568)
were candidates. Say if you want any of them tried in place of ours.

## Your answers and changes (owner 2026-11-16)
1. **Never-take, as a stand-in.** Every card now records how it was picked (`why`): "lost: its bootstrap trade hit
   the stop", "never take (stand-in): a single-level zone traded against the day's direction so far", its fallback,
   or "spread: …". It is in the card, `round.json` and the answers, and on the result card after you answer (hidden
   before, like the stratum).
2. **The round defaults are kept** (SPEC §13 Q-review-sample (2), (3)).
3. **Levels are coloured by role.** Grey only until a level has a role; MASTER §3.6 updated.
4. **The dial:** your notes didn't come through (the message ended at "My notes from looking:"), so the dial is
   unchanged.

**Preview rounds:**
- `round-new --preview` keeps a round apart: `<data_dir>/reviews/preview/rounds/<round>/`, with its answers in
  `reviews/preview/answers_<round>.parquet`, every row marked `preview`. `data/reviews/*.parquet` therefore holds real
  answers only.
- The page shows a PREVIEW chip on such a round.
- `preview-b958` was redrawn this way. It had no answers, and the old copy in the real-rounds folder is gone.
- **Real rounds leave out every touch any preview card can show** (its own and every other touch on its chart, before
  and after the answer). A touch counts as the same when it is on the same day, in the same direction, with its
  touching trade within 60 s, since builds differ by a few trades. Each real round records how many were left out
  (`excluded_preview_touches`).
- **Tested:** `tests/test_review_store.py`, through `draw_round` itself.

**CLAUDE.md:** working rule 15 (processes are stopped only by the PID of one I started).

## 21st.dev candidates for these four elements
I fetched and tried none of them in this stage. The reasons come from the step-14a shortlist's code reading
(`design-system/shortlist/03-performance-review-chart-data-moments.md`), plus why I built ours instead.

**Chart overlay.** No candidate draws on Lightweight Charts, so the overlay itself had none. For the chart's chrome:
- **Candlestick 43205 and Candle Chart 20107:** they draw their own seeded series (no data input), so they were layout
  references only.
- **Toolbar 19516:** it needs an old pre-1.0 Base UI package, which it doesn't declare.
- **Toggle Group Animated Toolbar 30343:** it calls the Base UI API on the Radix primitive, a bug.
- **Group-multi Toggle 29206:** usable (its group label is hard-coded); I wrote plain toggle chips instead.
- **Toggle Group 44138:** its CSS isn't included and it uses another icon set.
- **Toggle 31280:** it injects a `<style>` at run time, which the CSP blocks.
- **Expand Button 35286:** usable; I used a plain icon button.

**The dial:**
- **Radar Chart 29837:** recharts (heavy), its helper file not returned, mouse-only.
- **Radial Orbital Timeline 1820:** a React update 20 times a second, no keyboard.
- **Radial Chart 28963:** a recharts radial bar.
- Nothing fit as code, so canvas it is (MASTER §11).

**Replay bar:**
- **Video Player 30231:** it adds a Radix slider, slot and cva, and its 0.3 s control fade ignores reduced motion.
- **Playback Scrubber 35685:** the closest. A native range input, as ours is, but its CSS isn't included, so I
  styled the native input directly.
- **Slider 10324:** framer-motion, plus dependencies it doesn't declare.

**Answer panel and result card:**
- **Swipe Deck 23568:** its drag-to-decide had to go, since the reason comes first.
- **AI Approval 23792:** the closest pattern, with the answer expanding into the result; not tried.
- **Card Stack 35044:** its CSS module and motion tokens weren't returned.
- **Textarea 29430:** a Base UI dependency.
- **Textarea 23672:** its Field helper wasn't returned.
- **Rating Scale Group 8241:** not code-checked.
- **Presence Fade 18251:** a small wrapper doing what Motion already does here.

Worth trying if you want them: 29206 (layer toggles), 35286 (full screen), 35685 (scrubber), 23792 (answer → result).

## Review fixes (owner 2026-11-16)
**Evidence:**
- [`build/stage4br/`](build/stage4br/) ([`checks.json`](build/stage4br/checks.json)): the real preview round, at 1440,
  1280 and 960 px.
- [`build/stage4b/`](build/stage4b/): a synthetic panel, including the answer and the reveal.
- **Both: all checks passed.** Stages 4 and 4s re-ran clean.
- **The decision-time cut**, checked through the API on **all 60 preview cards** and the 5 synthetic ones: before an
  answer, no bar, level step, role change, marker, zone point, touch, map snapshot or King switch is after the
  decision, and no post half, outcome, stratum or pick reason is sent.
- The dial is approved as it is.

1. **Price axis on a symbol switch.** Each symbol now fits its own candles: switching resets the price scale.
   - **Check** (`fix1_*`, a browser test in `shoot.mjs`; the front end has no unit-test runner): on ES, drag the price
     axis (which turns its autoscale off), switch to NQ. Autoscale comes back and all 120 highs and lows of the last 60
     bars are on screen.
2. **One Focus | Table switch.** Exactly one is selected, and the chart opens on Focus (`fix2_*`).
   - **Before removing anything:** the old "every level at full strength" view would go with the toggle. It is kept on
     the **A** key for now (named in the Focus option's hint) until you say whether it goes.
3. **GEX orbs across the chart.** The node lines and the orb column are gone.
   - **What is drawn:** at every minute from 09:30, one small orb per node at its chart price (strike × the R in force),
     from the board known at that minute only.
   - **Size and glow** grow with its share of that board's largest node. Glow only from 25%; the King is the brightest,
     with a ring.
   - **Measured:** no orb after the replay clock or the decision.
   - **4× replay:** the 95th-percentile frame is 16.7 ms at all three widths, 0–3 frames over 20 ms out of about 280.
     One of four runs at 1280 px had 16 single dropped frames while step 13c's builds were loading the PC.
   - **How:** the orbs come from pre-rendered sprites into a cached layer. Lines and zones draw only what is on screen
     and build their hover facts only when hovered; each layer now takes under 2 ms (it was 20–47 ms).
4. **Card strip.** Arrows at both ends, faded edges while there is more to scroll, and the current card kept in view
   (checked: card 50 after two arrow clicks, inside the strip). Nothing is pushed off screen at 1440 or 960 px.
5. **No gap under the chart.** At 1280 px and wider the chart card is as tall as the column beside it; measured: the
   same bottom edge to the pixel. The replay bar moved under the toolbar, so it never sits under the dock.
   - **Trade-off:** at 900 px tall, the plot's lower part sits behind the open dock until you scroll (or minimize the
     dock).
6. **Hovering.**
   - **What shows:** any drawn object under the pointer (zone, line, high/low, orb, touch, crown, trade line) within
     4 px, all in one card sorted by price: name, price (and band), state, the level's role timeline.
   - **What doesn't:** no OHLC box, and nothing right of the last bar (checked at five heights).
   - **Placement:** the card never crosses the price axis (measured).
   - **The axis** keeps only the last price and the entry, stop and target tags (each named on the tag).
   - **What went:** the level labels in the right margin. Their names now show on hover and in the Table.
7. **From 18:00.** The chart and the replay slider start at 18:00 ET the evening before. **All 60 preview cards have
   candles from 18:00**, so none is short. Overnight levels, zones and touches follow the same cut. Restart still
   goes to the decision − 45 min.
8. **Skip to the end.** A button after step-forward, and the End key: to the decision before the answer
   (`fix8_*`), to the end of the revealed path after it (`fix8_after_answer`).

**Also:**
- **Card size:** about 1.8 MB now, with the night and the whole session's boards (the preview round is 71 MB on the
  SSD).
- **A crash fixed:** changing cards no longer throws "Object is disposed". The trade's axis tags were removed after the
  chart itself.

## Review round 3 (owner 2026-11-16)
**Evidence:**
- [`build/stage4r3/`](build/stage4r3/): the stacked list, the replay bar at the bottom, a hover over orbs, the shortcut
  sheet, at 1440 and 960 px.
- `stage4br` / `stage4b` / `stage4` / `stage4s` re-ran on the final build. **All checks passed.**
- **The cut:** the preview round was redrawn (the cards gain the stacked list), then all 60 cards were checked through
  the API. Before an answer nothing is after the decision, and each card's stacked list adds up to its count.

1. **Replay bar** back under the chart. The open dock may cover it.
2. **Hovering** shows only levels, highs/lows, node orbs and touch markers, each with its price. Zones, crowns and the
   trade lines no longer react. Checked: the cards over lines and orbs hold only those kinds.
3. **Stacked in the zone** (new, at the top of the detector readings). The list comes from the scorer's own count per
   level type at the decision (`stack_count`, `n_<type>`).
   - **Which levels:** for each type, the level(s) whose band at the decision lies in (or nearest) the zone.
   - **What it shows:** name, price (band centre), role, and state: provisional, provisional and crossed back, settled
     and flipped ×n, or original role. All from the state at the decision only.
   - **Gaps:** a member the chart can't find is listed as missing, and a type the counts don't name as "other". The
     facts panel's "Stacked" line now names the same members.
   - **Tested** (`tests/test_review_cards.py`): the VWAP is provisional at 11:25 although it is reclaimed at 12:30, and
     settled on the 14:05 card.
4. **Orbs** are vertical ovals, every one taller than wide (measured: 0 not). The weakest are about 80% of a bar wide
   (4.5 px at 5.5 px a bar), so their rows read as near-continuous lines; height and width grow with the share, up to
   about 6 × 12 px for the King.
   - **A correction on the way:** my first oval version made the King's row a thick 16-px band that took over the chart,
     so the heights and the glow were brought down.
   - **4× replay:** 16.7 ms at the 95th percentile at 1440, 1280 and 960 px, 0–1 frames over 20 ms.
5. **The A key** (every level at full strength) is listed in the `?` sheet under a new Review group, with T / S /
   Ctrl+Enter / Space / ← → / Home / End / P N / L.

## Review round 4 (owner 2026-11-16)
**Evidence:**
- [`build/stage4r4/`](build/stage4r4/) ([`checks.json`](build/stage4r4/checks.json), all passed): the replay bar, hover
  cards near the four corners and a long role timeline in a 272-px pane, the orbs, the stacked group, the Table's
  timeline, at 1440 and 960 px.
- `stage4`, `stage4s`, `stage4b` and `stage4r3` re-ran clean.
- `stage4br` passes everything except the 4× frame-time check (below). Its cut check covers every unanswered preview
  card: nothing after its decision.
- **The preview round was not redrawn:** you answered c01 and c02 on it (kept in `reviews/preview/`), and nothing in
  this round needed new card data.

1. **Orbs 20% smaller,** the same proportions. Every one is still taller than wide. The weakest are about 62% of a bar
   wide (3.5 px at 5.5 px a bar), down from about 80%, so their rows are dotted rather than near-solid.
2. **Hover cards always fit.** A card is measured before it shows, then placed right or left, below or above the
   pointer, wherever it fits whole in the chart's pane.
   - **Shortening:** a card taller than the pane shows each level's latest role events with "+n earlier (all in the
     Table)", then fewer objects ("+n more here").
   - **The Table** has a new Timeline column with the full timeline.
   - **Checked:** near the four corners and in the middle at 1440 and 960 px, and a Daily POC with 11 role events in a
     272-px pane ("+6 earlier"). Every card stays whole inside the pane.
3. **No King-switch crowns.**
4. **Stacked levels** are now a readings group like the others: "Stacked (n)", names left, prices right coloured by
   role, a muted note only for provisional, crossed back or flipped ×n.
5. **Replay bar** on one centre line: buttons, speed, scrubber thumb and track, and the clock text (measured: 0 px
   apart).
   - The scrubber is our own track, fill and thumb, over a transparent native range that keeps the keys and screen
     reader. The pattern follows 21st.dev's Playback Scrubber 35685 and the Video Player 30231 transport; no code was
     copied.
   - Ticks mark 09:30 and, after the answer, the decision.
6. **The stacked members** (see SPEC §13 Q-review-stack):
   - **What the scorer stores:** only counts per type, not which levels.
   - **How membership is decided:** a golden or gameplan zone counts every active level whose band overlaps it. That is
     why c01's QQQ 611 and VWAP lines count, though their centres are outside: their bands overlap. A level zone's
     members sit inside its union of bands.
   - **Audit of the 60 preview cards:** the current list matches every count on 10 of 10 golden cards and 33 of 50
     level cards. The proposed "inside, then overlapping" rule would match all 60.
   - **Not changed:** this waits for your decision.
7. **Top 5 nodes per ticker per minute** (QQQ and NDXP on NQ; SPX and SPY on ES), from that minute's board only. The
   readings, radial, Table and hover shares use the whole board. Measured: at most 5 orbs per ticker-minute.

**4× replay frame times:**
- **Replay alone:** 60 fps (0–7 slow frames of about 255).
- **With the test's scripted pointer moving across the chart:** 5–20% slow frames in most runs (95th percentile up to
  33 ms; one run at 1280 px stayed under 16.8 ms), while step 13c held the CPU at 70–100%.
- **Measured in that state:** the hit test takes 0.1 ms (at most 0.5), there are no long tasks, and the slow pointer
  events spend under 1 ms in our code; the rest is rendering under load.
- **Made cheaper this round:** the card re-renders only when its objects change, its position is set directly, the
  chart redraws for hover only when the highlighted line changes, and pointer updates are coalesced to one per frame.
- **Open:** I'll measure again once 13c ends.

## Review round 5 (owner 2026-11-16)
**Evidence:** [`build/stage4r5/`](build/stage4r5/) ([`checks.json`](build/stage4r5/checks.json), all passed) at 1440
and 960 px; `stage4`, `stage4s`, `stage4b`, `stage4r3`, `stage4r4` re-ran clean; `stage4br` clean except the 4×
frame-time check at 960 px (open since round 4, measured under 13c's load); its cut check: 0 of 60 cards show anything
after the decision before the answer.

1. **Nodes readings:** "Pika nodes (+)", "Barney nodes (−)" and "Pika member" are gone from the page (display only).
   Model and Order flow sit on the same columns as Regime and Freshness, in one row, on c01 (7 stacked) and c03.
2. **Replay text:** always "HH:MM ET" over "decision HH:MM:SS"; at the end only the decision line turns cream; the
   block is 128 px wide at every position, so the scrubber keeps its length (518 px at 1440).
3. **Stacked members:** the inside-first rule is built with a test. The preview round's stored lists were recomputed
   in place (`nqbot review restack preview-b958`, only the `stack` field): the same levels on all 60 cards; level
   zones 105 inside / 7 overlapping, golden zones 45 overlapping / 4 inside, none outside. The price hint now says
   whether the band is inside or overlapping the zone. Exact recording is a pre-13d item (SPEC §13 Q-review-stack).
4. **Orbs:** weakest 82% of a bar wide (4.5 × 5 px at 5.5 px a bar), every orb taller than wide; the largest 9 px tall.
5. **Replay window:** the slider runs from 30 minutes before the decision's minute (10:30 for 11:00:10) to the
   decision, and after the answer through the revealed path (c01: 10:09 → 11:08); Restart and Home go to its start;
   at the start the chart still has every candle from 18:00 (991 on c03).

## Review round 6 (owner 2026-11-16)
**Evidence:** [`build/stage4r6/`](build/stage4r6/) ([`checks.json`](build/stage4r6/checks.json), all passed) at 1440
and 960 px; `stage4`, `stage4s`, `stage4b`, `stage4r5` re-ran clean.

- **Step buttons** (and ← / →): paused, one click moves 1 / 2 / 4 bars at 1× / 2× / 4×, forward and back, and stays
  paused. While playing, a step jumps by the same amount and playback goes on from there (1×: +1, then still moving;
  4×: +4 plus the tick in between). Back at the window's start stays there; at the decision, forward stays at the
  decision before the answer; after the answer it steps on through the revealed path (c01 at 4×: +4 bars).
- The buttons say what they do: "Back 2 bars" / "Forward 2 bars" at 2×; the shortcut sheet says the same.
- **Preview answers** (c01–c10, test inputs) deleted; the shown touches stay excluded (SPEC §13).

## Review round 7 (owner 2026-11-16)
**Evidence:** [`build/stage4r7/`](build/stage4r7/) ([`checks.json`](build/stage4r7/checks.json), all passed);
`stage4b`, `stage4r3`, `stage4r4` re-ran clean.

- **One orb per node in the hover card:** on the densest orb row (1440: QQQ 612, 68 orbs; 960: QQQ 610, 54 orbs) the
  pointer stepped along the row, on orbs and between two of them (12 points each): no card held a node twice, every
  card showed the orb nearest the pointer with its own minute's board, and moving along the row showed 6 different
  minutes. Other nodes and levels under the pointer still all show, sorted by price.

## Review round 8 (owner 2026-11-16)
**Evidence:** [`build/stage4r8/`](build/stage4r8/) ([`checks.json`](build/stage4r8/checks.json), all passed) at 1440
and 1280 px (c01 with 7 stacked levels and c03 with 2) and 960 px; `stage4`, `stage4r5` re-ran clean (two checks
that needed an answered card now record a skip: the preview answers were deleted).

- **No description under the dial.**
- **Both boxes 496 px tall**, the readings' own height; 1 px between the readings' last line and their bottom padding.
- **Centred:** the ticker switch and the dial sit 17 px from each side and 68 px from the heading above and the box's
  bottom edge below. Not the same on all four sides: the box is 400 px wide (the readings need the rest of the row
  to keep three columns at 1280 px) and the readings make it 496 px tall, while the dial's drawing is wider than tall
  (366 × 284 px), so the room left over is vertical. Equal padding all round would need a GEX box about 540 px wide,
  which fits at 1440 px only (the readings' columns would narrow to about 236 px).
- **Ring labels** 11 px, brighter, on a dark halo, kept inside the canvas.

## Review round 9 (owner 2026-11-16)
**Evidence:** [`build/stage4r9/`](build/stage4r9/) and [`build/stage4r9t/`](build/stage4r9t/) (all passed) at 1440 and
1280 px (c01 and c03) and 960 px; `stage4r5`, `stage4r6` ran on a scratch copy of the round (below), `stage4r7` re-ran
clean.

1. **GEX header:** title left, ticker switch right, on one row; the title sits on the readings' title line (0 px
   apart). The header row is the switch's height (28 px), so the box's top padding is 9 px instead of 16. The dial is
   centred under the header (85.5 px above, 86.5 below); the box stays 400 px wide (17 px at each side). The title is
   "GEX SNAPSHOT · HH:MM ET", the time of the board on the dial: at a replay minute of 10:09 the board read at its
   close, 10:10; at the end of the replay before the answer, the decision (10:39 for 10:39:00), never later. The orb
   hover's "board at" line takes the same limit (it read one minute past the decision on the decision's own minute).
2. **Readings' bottom gap:** already the same as the top (18 px from the box top to the title, 19 px from the last
   row to the bottom, text to edge); unchanged, now checked within 2 px. Both boxes 496 px tall.
3. **Answered-card checks no longer skip:** `ui/web/scripts/review_scratch.py serve --round preview-b958 --port 8735`
   copies the round's cards (never its answers) to `<tmp_dir>/review_check_<time>/` as round `scratch-preview-b958` and
   serves the panel there; the stage answers c01 through the page (Take, a reason, Ctrl+Enter) only on a `scratch-`
   round, checks the result card ("Stop loss −0.96R … You said take"), then runs the after-answer checks (the slider
   10:09 → 11:08, steps past the decision); the panel was stopped by its PID and `review_scratch.py cleanup` removed
   the folder; `reviews/preview/` has no answers file.
