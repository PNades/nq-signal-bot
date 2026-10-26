# Step 6 review: the feature spec in plain English

*For the owner. Written 2026-10-05 at the end of step 5. Reading time about 30 minutes.*

> **Outcome (2026-10-05): approved.** You answered all 39 questions, plus three follow-ups (Q40–Q42, all yes): as recommended, except Q6 = (a) one tick at a time and Q9 = (b) Gaussian. Q37 confirmed, and the Q39 vote time is kept as listed. Follow-up: a POC tie between two prices equally near the middle of the range goes to the one nearer the volume median. The step-6 gate passed. The answers are in the feature spec, `config/` and the SPEC. This document is kept as the record of what was approved.

## How to use this document

Step 6 is a gate: nothing is coded from the feature spec until you approve it. This document has four parts:

1. **What the bot will measure** (sections 1–12): a plain-English summary of `docs/feature_spec.md`. You don't need to read that file. It holds the exact rules and the unit-test examples.
2. **How tuning works, and what it costs** (section 13): the menus ("grids") the data will pick from, and how many nights each takes on your laptop.
3. **The questions** (section 14): 39 numbered questions, each with my recommendation. They are the same, word for word and with the same numbers, as SPEC §13 "Step 6".
4. **The calendar check** (section 15): what to look at in `config/calendar.yaml`.

**How to answer.** One line is enough, e.g. *"All as recommended, except Q8 = b and Q37: Powell also spoke on 2026-01-xx."* Anything you don't mention is taken as the recommendation only if you say "all as recommended". If you want to change a definition rather than pick an option, say it in your own words and I'll write it up.

**What is already settled.** Everything you answered on 2026-10-04 and 2026-10-05 (node types, rugs, pika clouds, rolling, air pockets, order flow and the rest) is already written into the spec and isn't asked again.

---

## 1. The bot in one page

Every time NQ touches a level during the NY session, the bot asks one question: **if I take this trade with this stop and this target, how likely is the target to come first?** Then it decides whether that trade is worth taking.

- **Levels** come from two places:
  - your Skylit maps (QQQ and NDXP for NQ; SPY and SPX on ES as confirmation);
  - the chart: VWAP and bands, volume profile, single prints, highs and lows, the opening range and its golden zone, dark-pool prints and your gameplan zones.
- Levels at almost the same price merge into one **zone**. A touch of a zone is an **event**.
- For each event the bot considers four ways in:
  - (a) absorption;
  - (b) another order-flow trigger;
  - (c) a failed audit or sweep reclaim at the zone edge;
  - (d) a plain entry on the touch.
- It also considers every sensible structural stop and every structural target.
- Four models learn from history:
  - a **structure model** (does this zone hold?);
  - a **trigger model** (is this order-flow signal "the moment"?);
  - a **day-type model** (trend or range day?);
  - the **pair scorer**, which combines them and gives, for each stop/target pair, the chance of target first, stop first, a breakeven exit (when breakeven is on), or neither by the close.
- The bot takes the pair with the best expected R if it clears the thresholds, sized so a full loss costs at most the account's risk amount.
- Hard rules are only your rules: the setup windows, the news blackouts, the risk limits, the 25%-of-King cutoff and the 1:1 minimum. Everything else is learned.

The rest of this section-by-section summary explains what each measurement means.

---

## 2. Time and "no peeking"

- Every number the bot uses at a moment *t* must have been knowable at *t*. Examples:
  - a 1-minute bar labelled 09:31 can only be read from 09:32:00;
  - a Skylit board counts only from the moment the live bot would actually have received it;
  - yesterday's dark-pool prints count only from this morning's pre-open;
  - the daily vol data is yesterday's row, never today's.
- A test shifts all data by one second and one bar and checks that nothing at *t* changes.
- **Sessions:** RTH is 09:30–16:00 ET (13:00 on early-close days). Flat-by is 15:59, last entry 15:45. Holidays are skipped everywhere.
- **Contract rolls:** on the first day of a new contract, every level carried from earlier days is shifted by the price gap between the old and new contract, the same way Deepcharts back-adjusts.
- **Scaling:** every distance is stored three ways: in points, in ATR units (the average daily range of the last 14 sessions) and in expected-move units (from Skylit's implied vol). A 20-point move means something different on a quiet day and a wild one.
- **ES equivalents:** any NQ-point setting used on ES is converted at that day's ES/NQ price ratio (your answer, 2026-10-05).

## 3. Putting strikes on the NQ chart

- A QQQ strike becomes an NQ price by multiplying by R = NQ price ÷ QQQ price, taken at the moment of that board.
- The board's levels stay fixed until the next board.
- **Glitches:** a ratio that jumps more than 3 basis points from the recent median is ignored, unless it repeats three times in a row (a genuine shift).
- **Opening boards:** NDXP and SPX boards from the first 2 minutes after 09:30 are skipped, because the index prints are unreliable then.
- **Node bands:** every node is a band, not a line: QQQ ±$0.50 (≈ ±20 NQ pts), NDXP ±20 (you confirm it at step 13), SPY ±$0.50, SPX ±5.
- **Order prices** are rounded to the tick in the safe direction:
  - stops away from entry;
  - targets toward entry;
  - limit entries to the passive side.
- **Step 8b gate:** checks the mapping against your Atlas screen before any of this is used.

## 4. The Skylit map

**Sizes and types (your definitions).**
- The **King** is the biggest node on a map, by absolute size; it can be negative. (Owner 2026-10-25: "who is the King" is now the *confirmed* King, the strike that has led over the last 60 s by the rule under Rolling ceilings below, so a 760/759 tug-of-war doesn't flip it. % of King is still against each board's own biggest node, so it never exceeds 1.)
- A node is **relevant** once it reaches 25% of the King. It stays relevant until it drops below 20%, so it doesn't flicker on and off.
- **Gatekeepers** are relevant nodes sitting between price and the King (owner 2026-10-25: the confirmed King; kept even though Skylit's own examples label differently, because Skylit's "gatekeeper" just means any non-King node ≥ 25%, so it is compared with your Gatekeeper + significant together).
- **Significant** = any other relevant node. **Normal** = everything smaller.
- **Colour is separate:** pika (yellow, positive) or barney (purple, negative).

**State of each node.**
- **Role:** price below the node makes it a ceiling (short); price above makes it a floor (long).
- **Crossed:** price trades past the band. The setup is dead, and that counts as "broke" for learning.
- **Flipped:** a 5-minute bar then settles beyond the band, and the node takes the opposite role.
- **Reclaimed:** after a cross, price comes back to the original side and moves `rearm_dist` (10 pts) away. The node is alive again in its old role.
- **Beach ball:** a cross followed by a reclaim. Its depth and duration are recorded.

**Map patterns (all are model inputs, never trade rules).**
- **Velocity and lean:** how much each node grew or shrank over the last minute, and whether growth is mostly below price (a pull down) or above (a pull up), averaged over the last minute (your setting: accumulation shifts quickly). (Owner 2026-10-25: the lean uses growth in size, so a negative node going from −1M to −2M counts as building; lean = above minus below, so positive means bias up, everywhere.)
- **Magnets:** each node pulls with strength = size ÷ distance. The bot sums the pull above and below price.
- **Rolling ceilings and floors:** the biggest node above price is replaced (for at least 60 s) by a closer one that is still above price, whether the old one faded or the new one grew. Mirror image for floors. Per your answer, a roll stays active (bearish for a ceiling roll-down, bullish for a floor roll-up) until it rolls back to where it started, with no timer. If price trades through the current ceiling's (or floor's) zone, the roll is paused until price comes back fully below the ceiling (above the floor). If, while it's paused, a new ceiling forms above price and that one rolls down, that's a new active roll that replaces the old one. The bot also tracks whether the node mass above and below price is shrinking (since the open and since the roll began).
  - *Owner 2026-10-25:* a ceiling (and the King) changes only when the current one led less than a quarter of the last 60 s and isn't leading now; it moves to the strike that led longest. So two strikes swapping every 30 s never change it, and a fading one never gets stuck. With 60 s a clean new leader takes over about 46 s after it starts leading. Price moving through the ceiling's zone (on NQ, not the QQQ spot) changes the ceiling at once and is never a roll; a switch counts as a roll only if the new strike sat above price the whole minute. The roll's net strikes can go negative (the ceiling ended up above where the roll started). The 60 s is now two settings, one for rolls and one for the King, each tested at step 13 on months 1–4 against 30, 120 and 300 s, kept at 60 unless another value is clearly better.
- **Gamma regime:**
  - net gamma within ±1% of spot (your setting), as a share of the King;
  - the share of that gamma that is positive (the "yellow vs purple" read).
- **Rug:** a relevant positive node with a negative node at most one strike below it, price below. **Reverse rug:** the mirror. (Owner 2026-10-25: a value of exactly 0 isn't a partner; "directly below" means the next listed strikes.)
- **Pika cloud:** 2 or more adjacent positive strikes, each at least 10% of the King (owner 2026-10-24: now each a relevant node, entering at 25% and staying until it drops below 20%).
- **Air pockets:** runs of strikes under 2% of the King (owner 2026-10-24: now 10%), which price travels through quickly. The bot also records how much of each pocket is negative.
- **King dominance and migration:** how much bigger the King is than the second node, how far away it is, and how often (and in which direction) the King has moved today. Stair-stepping is a trend tell.
- **Rainbow road:** many positive and many negative relevant nodes with no dominant King.
- **Whipsaw:** the three Trinity maps lean in different directions while the Nasdaq map still has a floor and a ceiling (owner 2026-10-25: once with QQQ, once with NDXP).
- **Trinity:** SPY, SPX and the Nasdaq side (QQQ or NDXP) each have a node in the same role right at their own price, at the same moment. (Owner 2026-10-25: a crossed node counts in its new, provisional role, so retests after a break aren't marked down; it stops counting if price crosses it back.)
- **S&P obstacles:** relevant SPY/SPX nodes between ES and the trade's target (converted to ES), counted as obstacles.

**Context.**
- **Daily vol (Tempest):** QQQ/SPY implied vols, term structure, skew, and realised vs implied vol.
- **Calendar flags:** OPEX week, quarter-end, CPI/FOMC/NFP/PPI/retail-sales days, half days, minutes to the close and to the next news blackout.

## 5. Chart levels

- **VWAP** from 18:00 (the CME day) and from 09:30, with 1σ and 2σ bands. Whether the 09:30 VWAP gets bands too is **Q8**.
- **Volume profile** for the day (from 18:00) and the week (from Sunday 18:00), updated every minute:
  - the **POC** (most-traded price) is a level;
  - the **value area** (70%) gives the **VAH zone** (VAH up to the profile high, "premium") and the **VAL zone** (profile low up to VAL, "discount"). These are context, not levels: shorts in premium and longs in discount are flagged;
  - **LVNs** (thin spots in the profile) are levels.
- **Single prints:** price bins traded in only one 30-minute period. They stay levels until filled, across days.
- **Highs and lows:** overnight high/low, previous day, previous RTH, previous week, IB high/low (from 10:00), and "retested extremes" (an older high or low that a later touch held).
- **Breaking a chart level:** a 1-minute candle *settling* beyond the band (open and close both beyond), as you described. Trading through without settling isn't a break.
- **Sweeps at highs and lows:** price may trade past the level and come back within 3 one-minute bars. That counts as a hold, unless it goes more than 15 pts past, which is an immediate break.
- **Band widths for chart levels:** you said the data should decide.
  - Each level type gets a starting half-width of 4 pts, capped at 12 (single prints: the unfilled run itself, plus up to 6).
  - At step 13 the bot measures how far price really pokes past each type before reacting, and sets the width from that.

## 6. Dark-pool levels

- Each morning: the 3 biggest QQQ prints by dollar value over the last 45 days, up to yesterday's close, become NQ levels with a ±$0.50 band (you confirm the band at step 13).
- SPY prints play the same role on ES, as confirmation.
- They break like chart levels (a settled 1-minute candle).

## 7. Zones, events and confluence

- **Zones:** levels whose bands overlap, or whose centres are within 10 pts, merge into one zone, up to 60 pts wide. The golden zone and gameplan zones never merge: levels inside them are counted as their "members".
- **Touches:**
  - a zone is touched when price comes within 5 pts of it (`zone_proximity`);
  - it re-arms only after price has moved 10 pts further away;
  - approaching from below = short, from above = long.
- **Crossing:** each level in a zone breaks by its own rule. The zone carries on until every member has broken.
- **Confluence:** every event records what is stacked in its zone:
  - how many levels and which types;
  - node sizes and types;
  - the zone's width and how centred the touch was;
  - the distance to the next zone above and below.
  The model learns which combinations matter. There are no hand weights.
- **Freshness:** how many times each level has been tested today (and since it was created), how many of those tests held, and how long ago the last one was.
- **Day memory:** how today's earlier touches turned out (only those already resolved), and yesterday's day type.
- **Cross-market:**
  - ES sitting at its own stacked level at the same time;
  - NQ/ES divergence (one makes a new 15-minute high and the other doesn't);
  - relative delta (NQ buying pressure vs ES's over the last minute).
- **Touch groups:** if one trade touches two zones at once (e.g. the golden zone and a POC inside it), they count once for learning and trade at most once.

## 8. Entries and order types

| Style | When it decides | Default order |
|---|---|---|
| (a) absorption | when absorption fires (step 19) | marketable limit (fills now, slip capped at 4 ticks) |
| (b) other order flow | when exhaustion, delta divergence or a stacked imbalance fires (step 19) | marketable limit |
| (c) audit / sweep reclaim | the close of the 1-minute bar that closes back outside the zone on the approach side, or back inside the range after a sweep | stop-limit 1 tick past that bar's extreme |
| (d) plain | the touch | limit at the zone edge |

- **Audits:** as you said, price can go as deep into the zone and stay as long as it likes. Only the zone's own break kills the audit.
- **Order types:** the backtest tries every order type for every style and picks the best on months 1–4 (style (a) never uses a passive limit).
- **Order life:** every pending order has a time-to-live and is pulled when its zone breaks, at a news blackout, on a data problem, on `!pause` or at 15:45.
- **Fills in the backtest are pessimistic:**
  - a limit fills only when price trades *through* it;
  - a target counts only when price trades a tick through it;
  - stops slip 1 tick (measured properly at step 13);
  - every order arrives 1 second after the decision (measured at step 21).

## 9. Order flow (built at step 17, used from step 19)

- **Scope:** only computed while price is inside a zone's touch band or a VAH/VAL area. Away from structure, order flow is ignored.
- **Absorption (style a):**
  - lots of aggressive buying (≥ 300 contracts in 30 s) within 2 pts of the visit's high, with no new high, and the ask refilling ≥ 3 times;
  - then price pushes ≥ 1 pt below that buying. That push is the entry signal;
  - mirror image at a floor;
  - per your answer, the high can be anywhere in the zone, and there's no time limit on the push.
- **Exhaustion (b):** a new extreme made on buying that at least halved compared with the 15 s before. Thin volume at the extreme is recorded as an input only.
- **Delta divergence (b):** price makes a higher high (≥ 2 pts) while cumulative delta doesn't.
- **Stacked imbalance (b):** ≥ 3 adjacent prices where buyers outnumber sellers 3:1 diagonally (or the reverse) over the last minute.
- **Other inputs for the trigger model:**
  - big trades (input only, as you said);
  - delta and the CVD slope;
  - top-of-book imbalance;
  - refills and pulls;
  - aggressive volume that gets nowhere;
  - seconds since the touch.
- **Area events:** a style (a)/(b) firing inside a VAH zone (shorts) or VAL zone (longs) with no level nearby becomes its own event.

## 10. Your golden zone

- **Opening range:** the high and low from 09:30. It is established at 09:50, can still be redrawn by new extremes until 10:00, and is locked from 10:00.
- **Orientation (your rule):**
  - low then high = an up leg: long pullbacks;
  - high then low = a down leg: short pullbacks.
- **The zone:** between the 0.5 and 0.79 levels, with 0.618 and 0.705 recorded too (0.786 changed to 0.79 by the owner, 2026-10-14).
  - Targets include the range extreme and the −0.272 / −0.618 extensions.
  - The stop candidate is the far extreme (the 1.0 level).
- **Invalidation:** a 1-minute bar settling past 0.79.
- **Repeat tests:** every return after re-arming is a new event, with no limit.
- **Trading window:** it is the only setup that can trade 09:50–10:00. Other levels still count as confluence inside it.
- *Owner 2026-10-26:* the start is now 09:45, with a 10:30 redraw and orientation flips (2026-10-16). The flip and the 10:30 redraw are now tested at step 13 on months 1–4: four versions (flip on/off × redraw on/off), and the current design (both on) stays unless another is clearly better on golden-zone trades (net R per month, its 95% interval above 0). With the flip off, a settle past 0.79 just ends the setup and the zone keeps its direction; with the redraw off, the 10:00 drawing and IB high/low stay all day. A count report splits golden-zone hold rates by flips (0 vs 1+) and by drawing (before 10:00 / at 10:00 / at 10:30).

## 11. Stops, targets, breakeven and what "success" means

**Stop candidates:**
1. the swing extreme since the zone last re-armed;
2. the zone's far edge;
3. the next structure behind the entry;
4. the gameplan stop;
5. ~5 pts past the absorbed area;
6. the golden zone's 1.0 level.

The bot adds a buffer (start 5 pts) to candidates 1, 2, 3 and 6 (candidate 5 already includes its ~5 pts). There is no minimum or maximum stop (your rule): only "at least 1 micro must fit".

**Targets:**
- every zone in the trade direction, at its near edge and at its centre;
- golden-zone extensions;
- the gameplan's T1/T2;
- only targets at ≥ 1:1 after costs.

The model scores every stop × target pair together and picks the best expected R. A fixed 1:2.5 target is kept only as a comparison.

**Breakeven:** your first-structure rule is one of four candidate rules. The data picks one per fold (section 13).

**What the models learn to predict:**
- **Structure "held":** after the touch, price moves 20 pts away (with a trade-through) before the zone breaks. "Broke" = the zone breaks. Neither within 30 minutes = timeout.
- **Trigger "good":** +5 pts in favour before −3 pts against, within 2 minutes of the order's arrival.
- **Day type:** trend if the day had ≤ 2 crosses of the 09:30 VWAP, ≤ 2 crosses of the POC, and closed in the outer 20% of its range on the side of the new highs/lows; otherwise range.
  - It is predicted every 15 minutes from 10:00 by a simple logistic model on 8 inputs: time of day, IB width, VWAP crosses, how one-sided time around VWAP was, how one-sided new highs vs new lows were, range vs expected move, NDXP regime, realised vs implied vol.
  - POC crosses and rotations are left out because they measure the same chop as VWAP crosses.

## 12. Key start values (full list: feature_spec F15)

| What | Start |
|---|---|
| Touch distance (`zone_proximity`) | 5 pts |
| Merge distance (`confluence_tol`) | 10 pts |
| Chart-level half-width / cap | 4 / 12 pts (single prints 0 / 6) |
| Stop buffer | 5 pts |
| Absorption: volume / near the high / window / refills / push | 300 contracts / 2 pts / 30 s / 3 / 1 pt |
| Imbalance: ratio / min volume / stack / window | 3:1 / 20 / 3 / 60 s |
| Exhaustion: lookback / decay | 15 s / halves |
| Divergence: lookback / new extreme | 60 s / 2 pts |
| Structure label: reaction / window | 20 pts / 30 min |
| Trigger label: good / bad | +5 pts / −3 pts in 2 min |
| Order life (styles a, b / c, d) | 5 s / 60 s |
| Breakeven offset | 1 tick |
| Recency half-life | 6 months |

---

## 13. How tuning works, and what it costs

**The principle.** Anything chosen from results picks from a menu written down *now*, before any result is seen. That stops the backtest from fitting noise.
- The setting with the best **money score** wins: average R per month minus one standard error, so a result resting on a few lucky days is discounted.
- That score must also keep three full losses in a row rare (≤ 15% of 3-trade runs).
- **Detector settings** are searched one parameter at a time, each menu including its start value. Trying every combination would mean thousands of runs and would mostly find luck.

**Overnight runs.** These are rough, based on the SPEC's own estimates (a full 12-month rebuild ≈ 1 night, a night ≈ 9 hours). Step 13 measures the real speeds, and every job prints its estimate before it starts.

| Menu | Choices | Size | When it runs | Nights |
|---|---|---|---|---|
| Thresholds (`threshold_grid`) | min EV {0.05, 0.10, 0.20, 0.30, 0.45} R × min P(target) {0.30, 0.40, 0.45, 0.50, 0.60} | 25 | every fold, every account sweep | minutes (a sweep < 1 night) |
| Breakeven | none · your first-structure rule · at +1R · at +0.5R | 4 | every fold | ≈ 0.5 once, + 1–2 hours per rule some fold picks |
| Entry-style sets | {a,b,c,d} {a,b,c} {a,b} {c,d} {c} {d} | 6 | every fold | minutes |
| Step-13 structure detectors | touch distance {2,5,8,12} · merge distance {5,10,15,20} · sweep depth {10,15,25,40} · sweep bars {1,2,3,5} · flip margin {0,2,5} for 3 level groups | 19 runs | step 13 | ≈ 2.5 |
| Step-13 execution, (c) and (d) | order type {limit, marketable, stop-limit} × order life {10,30,60,120,300 s} per style, then one stop buffer {1, 2.5, 5, 8, 12} for all styles | 34 re-runs | step 13 | ≤ 1 |
| Step-19 order-flow detectors | 5 absorption and 8 other settings, 3–4 values each (feature_spec F16.5) | 28 runs | step 19 | ≈ 4–6 |
| Step-19 execution | (a)/(b) order type × order life {2,5,10,30 s} · absorption stop buffer {2, 3.5, 5, 8} · absorption look-back {60,120,300 s} | 25 re-runs | step 19 | ≤ 1 |
| Recency half-life | 3, 6, 12 months, or none | 4 | once per pair scorer | ≈ 0.5 |
| LightGBM | tree size {7, 15, 31 leaves} × min leaf size {100, 500 rows}; everything else fixed and conservative | 6 | once per model | ≈ 3.5 |
| Ablation groups | trigger model · Trinity / cross-market · confluence · day type · area events · absorption stop · **map shape (new)** | 7 | once, step 19 | ≈ 1–2 per arm; first round ≈ 7–14 |

**By step:** step 13 ≈ 3–4 nights in all; step 15 (the first full walk-forward) ≈ 4–6 nights; step 19 ≈ 3–6 weeks of nights, mostly the ablation (the SPEC's 2–4 weeks didn't count the later elimination rounds).

**The ablation.**
- Each group is switched off in turn, and the whole system re-run on test months 7–9.
- A group that doesn't add money is cut, and the process repeats.
- I propose a **7th group, "map shape"**: the six Skylit concepts you approved on 2026-10-04 (beach ball, magnets, air pockets, King dominance/migration, rainbow road, whipsaw). They are new and untested, so they should be judged as a block. Lumping them in with "confluence" would muddy that test. It costs about one extra arm per round (Q30).

**Things not tuned (Q31, answered):** the lean window (1 minute), the regime band (1%) and the two rainbow-road settings (2 and 1.5) are fixed at your values; the roll window is gone (rolls stay active until rolled back). Why not tune them:
- They only shape inputs, and the model copes with any sensible value.
- Each extra tuned setting is another chance to fit noise.

---

## 14. The questions

The text below is identical to SPEC §13 "Step 6".

### Approvals

1. Approve the F15 start values and caps (46 rows, one of them, `roll_window_min`, since removed by your Q31 answer; config keys stay `null` until then)? Rec.: yes.
2. Day-type inputs (`daytype_inputs`, F15 row 45): time of day, IB width, VWAP crosses, VWAP side share, new-extreme skew, range vs EM, NDXP regime, RV/IV, leaving out POC crosses, rotations and the rest of the vol context? Rec.: yes.
3. Day-type label (§7.1): keep trend = ≤ 2 VWAP crosses, ≤ 2 POC crosses and a close in the outer 20% of the range on the new-extremes side? Rec.: keep.
4. `news_title_map` (settings.yaml) matches your high-impact list (13 blocking names, 9 non-blocking lines)? Rec.: yes.

### Definitions still open (F13)

5. FQ-01, ATR average: (a) simple mean or (b) Wilder? Rec.: (a), a scaling unit that needs no extra warm-up.
6. FQ-08, value-area expansion: (a) one tick at a time or (b) CBOT two-tick sums? Rec.: (b), checked against Deepcharts at step 12.
7. FQ-08, POC tie-break: (a) nearest the profile midpoint, (b) nearest the previous POC, (c) the lower price? Rec.: (a).
8. FQ-09, σ bands on the 09:30 VWAP: (a) yes, both anchors, or (b) the 18:00 anchor only? Rec.: (a); the model learns their worth.
9. FQ-10, LVN smoothing: (a) centred moving average or (b) Gaussian? Rec.: (a).
10. FQ-11, single-print runs: (a) may join bins from different periods or (b) one period only? Rec.: (b), the classic single print.
11. FQ-12, whipsaw's Nasdaq side: (a) QQQ and NDXP variants both as features, (b) NDXP only, (c) only when they agree? Rec.: (a).
12. FQ-13, retested-extreme candidates: (a) the registry highs/lows or (b) plus intraday swing pivots? Rec.: (a).
13. FQ-14, sweep window: (a) the break bar is bar 1 or (b) bar 1 is the first full bar after it (the break bar's own close can already reclaim)? Rec.: (b).
14. FQ-20, velocity lean: (a) time-weighted or (b) plain mean over boards? Rec.: (a) (it matters little now that `lean_window` is 1 minute).
15. FQ-22, retested extreme ends: (a) after `retest_lookback_sessions`, (b) when crossed and not flipped back, (c) whichever comes first? Rec.: (c).
16. FQ-22, does a retest in the extreme's own session count? Rec.: no, later sessions only (that day the level is already in the registry).
17. FQ-23, does a crossed (not yet flipped or reclaimed) node count toward Trinity? Rec.: no.
18. FQ-05 leftover, also measure rolls across sessions (yesterday's last board vs today's first)? Rec.: no, intraday only.

### Pre-declared grids (F16)

19. `threshold_grid`: `min_ev_r` {0.05, 0.10, 0.20, 0.30, 0.45} × `min_target_prob` {0.30, 0.40, 0.45, 0.50, 0.60} (25 points)? Rec.: yes.
20. Breakeven rules: none, your first-structure rule, +1.0R, +0.5R (4 rules, no regime split)? Rec.: yes.
21. `style_set_grid`: {a,b,c,d}, {a,b,c}, {a,b}, {c,d}, {c}, {d}? Rec.: yes.
22. Step-13 detector grids, one coordinate pass: `zone_proximity` {2,5,8,12}, `confluence_tol` {5,10,15,20}, `sweep_max_depth` {10,15,25,40}, `sweep_reclaim_bars` {1,2,3,5} (19 runs ≈ 2.5 nights)? Rec.: yes.
23. `flip_margin` tuned per 3 groups (GEX nodes · chart lines · highs/lows) over {0, 2, 5} pts instead of per level type? Rec.: yes.
24. Step-13 execution grids: per style (c) and (d), order type {limit, marketable limit, stop-limit} × `entry_ttl_sec` {10,30,60,120,300}; then one `stop_buffer` for all styles from {1, 2.5, 5, 8, 12} pts on the pooled (c)+(d) trades (≤ 1 night)? Rec.: yes.
25. Step-19 detector grids (F16.5: 5 absorption and 8 style-(b) parameters, 3–4 values each, 28 runs ≈ 4–6 nights)? Rec.: yes.
26. Step-19 execution grids: (a)/(b) order type × `entry_ttl_sec` {2,5,10,30}; `abs_stop_buffer` {2, 3.5, 5, 8}; `abs_stop_window_sec` {60,120,300}? Rec.: yes.
27. `recency_halflife_months` grid {3, 6, 12, none}? Rec.: yes.
28. LightGBM grid: `num_leaves` {7,15,31} × `min_data_in_leaf` {100, 500} rows, the rest fixed (F16.7)? Rec.: yes.
29. Ablation groups as defined in F16.8 (e.g. "Trinity" = every ES/SPY/SPX feature; "confluence" = zone makeup beyond the primary member)? Rec.: yes.
30. Add a 7th ablation group, "map shape" (beach ball, magnets, air pockets, King dominance/migration, rainbow road, whipsaw; ≈ +1 arm per elimination round)? Rec.: yes. **Answered 2026-10-05: yes** (the ablation keeps the group only if it adds money).
31. Keep `lean_window`, `roll_window_min`, `regime_band` and the two `rainbow_*` values fixed (not tuned at step 13)? Rec.: yes. **Answered 2026-10-05:** fixed; `lean_window` 1 min, `regime_band` 1%, rainbow 2 / 1.5; `roll_window_min` removed (a roll stays active until rolled back, no timer).
32. Pre-declare no regime splits for breakeven, entry order types or zone widths (regime results stay report-only)? Rec.: yes.
33. New config keys `lgbm_grid`, `ablation_groups` (settings.yaml), `tune_grids_13`, `tune_grids_19` (features.yaml)? Rec.: yes.

### `config/calendar.yaml` (built at step 4a, 2026-10-04; sources and details in its `meta.research_notes`)

34. BEA never published a Q3-2025 "advance" GDP; list its 2025-12-23 08:30 "Initial Estimate" as Advance GDP? Rec.: yes (the first print of the quarter).
35. 2025-10-01 – 11-12 shutdown: the funding lapse counts as the announcement for every BLS/BEA/Census release scheduled in it, so those dates carry no release (all 08:30, no tradeable window changes)? Rec.: yes.
36. CPI 2025-11-13 and PPI 2025-11-14 (scheduled just after the shutdown ended, never released) are treated as not scheduled? Rec.: yes.
37. Fed Chair appearances: every scheduled one on the Fed's calendar while Chair (Powell to 2026-05-15, Warsh from 2026-05-22); apart from the two Q39 items, only 2025-12-01 (20:00), 2026-03-21 (a Saturday) and 2026-03-30 (10:30, the only one inside trading hours) fall between 2025-10-15 and May 2026. Does that match what you remember? Rec.: confirm, or name any missing date.
38. Good Friday 2026-04-03 is a holiday (NYSE closed) even though the jobs report came out that day? Rec.: yes.
39. Add Warsh's confirmation hearing (2026-04-21 10:00) and the Senate confirmation vote (2026-05-13 14:40) as Fed Chair blackouts? He wasn't Chair yet, but ForexFactory rated both High, so live would have blocked them. Rec.: yes, so backtest and live agree. **Answered 2026-10-05: yes** (added to `calendar.yaml` events as Fed Chair blackouts).

### Follow-ups from the step-6 review (answered 2026-10-05: Q40–Q42 yes)

40. Value area (F3.3): on a volume tie add both sides, and once one side reaches the profile's edge keep adding the other side? Rec.: yes.
41. Zone widths and flip margins (F5.14): one shared setting for daily and weekly POC, for daily and weekly LVNs, and for the 18:00 and 09:30 VWAPs (the model still sees which one it is)? Rec.: yes.
42. LVN smoothing (F3.4): cut the Gaussian off at ±3σ (`lvn_gauss_trunc_sigma`, keeps 99.7% of its weight)? Rec.: yes.

*For information (no answer needed):* 2025-11-28 (Globex outage, early close) is kept (owner, 2026-10-04). The 2027 BLS, BEA, Census and ISM schedules aren't published yet, so the monthly refresh fills them in. 2027 FOMC minutes dates are derived and 2027 press conferences assumed. Funding runs to 2026-12-11, so a lapse could move the Dec-2026 releases. Optional: Bash deny rules for `.env` in `.claude/settings.json`.

---

## 15. What to check in `config/calendar.yaml`

The file was built at step 4a from the agencies' own schedules (sources and notes in its `meta` block). It drives the news blackouts in the backtest, so it has to match what was scheduled at 08:30 each morning.

1. **`events:`** Spot-check a few dates you remember, e.g.:
   - an FOMC day (14:00 decision, 14:30 press conference);
   - a CPI day;
   - an NFP Friday.
   Each `blackout: true` line blocks trading 30 minutes before until 60 minutes after.
2. **Fed Chair lines** (`name: Fed Chair`): only 2025-12-01, 2026-03-21 and 2026-03-30 are listed between mid-October 2025 and May 2026 (only the last is in trading hours), plus Warsh's confirmation hearing (2026-04-21) and Senate vote (2026-05-13), which you added on 2026-10-05 (Q39). Add any scheduled testimony or speech you remember (Q37).
3. **`holidays:` and `early_closes:`** Good Friday 2026-04-03 is a holiday (Q38). Early closes are at 13:00 ET (the cash close, when the option maps stop). 2025-11-28 is kept despite the morning Globex outage (your answer, 2026-10-04).
4. **`schedule_changes:`** the shutdown postponements (Q35–Q36) and the Q3-2025 GDP "initial estimate" (Q34).
5. **`news_title_map`** (in `config/settings.yaml`): your 13 blocking releases by their ForexFactory titles, plus 9 lines released at the same time that don't block on their own (Q4). Two notes:
   - FOMC Economic Projections and Personal Spending are listed as non-blocking so the live red-folder check doesn't mistake them for extra events;
   - ForexFactory renamed Powell's past Chair speeches "FOMC Member Powell Speaks". That doesn't matter, because past dates come from the Fed's own calendar.
6. **Nothing to do:**
   - 2027 BLS/BEA/Census/ISM dates aren't published yet; the monthly refresh adds them;
   - funding runs to 2026-12-11, so a lapse could move December 2026 releases;
   - the live bot also blocks any other US event ForexFactory marks red at 08:30 (live only, logged).
