# Gameplan glossary (for the parser)

What the words in the daily gameplan mean, and where each one goes in the §5.6 JSON.
Source: the owner's cheat sheet (`docs/gameplan_terminology.md`), plus the parsing rules the
owner set on 2026-10-07 (§13, feature_spec F8).

**The ground rule:** record what the gameplan says. This glossary explains words; it never
gives permission to guess. A missing price stays empty, an unclear word stays unclear, and
anything with no field goes word for word into `other_conditions`.

---

## 1. Line roles (the word at the start of a line)

| Word | Meaning | Store as |
|---|---|---|
| **BUY** | A zone where only an *audited hold* is bought. Never "touch it, buy it". | An entry, side `buy`. Trigger type `audited_hold` unless the line names another trigger. |
| **SELL** | A zone where only a *failed audit / failed auction* is sold. | An entry, side `sell`. Trigger type `failed_audit` unless the line names another trigger. |
| **PIVOT** | A two-way level that price rotates around. No standing trade. | Not an entry. Plan-wide `other_conditions`, word for word with its price. |
| **TARGET** | Where a move is aiming. Take-profit territory, not an entry. | A target of the entry or scenario it is written under. If it belongs to neither, plan-wide `other_conditions`. |
| **INVALID** | If price *accepts* beyond it, today's map is wrong: stand down and re-map. | The whole map's invalidation: plan-wide `other_conditions`, word for word. Attach it to an entry only when the line names that entry. Timeframe = acceptance (minutes as written, else 15). |
| **NOTE / OBS** | Reference information, usually ES confirmation levels. Never traded alone. | Never an entry. An ES gate only when the line ties it to a named entry; otherwise `other_conditions`. |

The direction of a trade always comes from its line's role (BUY/SELL) or, for a conditional
entry, from the direction of the break. The verb (section 2) only gives the trigger type.

## 2. The verbs (triggers and invalidations)

| Word | Meaning | Store as |
|---|---|---|
| **Test / touch** | Price reaches the level. Not tradeable by itself. | Never a trigger. |
| **Acceptance** | Price stays beyond a level: several closes, usually 10–15 min, with volume building. This is how a level really breaks. | Timeframe `acceptance` with the minutes as written; if no minutes are written, 15 (owner rule; the cheat sheet's "usually 10–15 min"). |
| **Failed auction / rejection / reversion** | Price pokes beyond, finds no business, comes back fast. A probe, not a break. | Trigger type from the line's role (on a SELL line: `failed_audit`). |
| **Audit** | The retest after a break. Held retest = *audited hold* (buy trigger). Failed retest = *failed audit* (sell trigger). "We trade the audit, never the first touch." | `audited_hold` / `failed_audit`. The "never the first touch" rule itself goes to plan-wide `other_conditions`. |
| **Break-plan** | A level tested three times becomes a breakout candidate. | A conditional entry (section 7). |
| **Lost / reclaimed / loss / initiation** | Not defined in the cheat sheet (see section 8). | Keep the gameplan's own word in the trigger note. Don't map it to another trigger type. |

## 3. Level labels (where the numbers come from)

Keep a level's source label word for word with its entry (e.g. "GEX 3 + BL8 weld"). Never
turn a label into a price; a price is stored only where the gameplan writes it.

| Label | Meaning | Store as |
|---|---|---|
| **HVL** | The volatility regime line. Above = positive gamma (calm, mean-reverting); below = negative gamma (fast, trending). | `hvl` |
| **HVL 0DTE / 0DTE HVL** | The same line built only from options expiring today. | `hvl0` |
| **Regime line** | The HVL volatility regime line ("0DTE regime line" = the HVL 0DTE area). | `regime_line`, as written. Never copy a value between `hvl`, `hvl0` and `regime_line`: each is filled only from its own words. |
| **VIX regime line (~19.5)** | A VIX gate, not a price level. | `other_conditions`. Never `regime_line`. |
| **CR / PS** | Call Resistance (heaviest call strike, ceiling) / Put Support (heaviest put strike, floor). Also the 0DTE versions. | Labels only. |
| **1D Min / 1D Max** | The day's expected-move envelope (about ±1 standard deviation). | Labels only. |
| **Gamma Wall 0DTE** | The 0DTE gamma wall. | Label only. |
| **GEX 1–10** | The ten largest gamma strikes, ranked (GEX 1 = biggest). Hedging shelves. | Labels only. |
| **BL 1–10 ("Blind Spots")** | Model levels where hedging pressure concentrates; ranked, recomputed daily. | Labels only. |
| **LB / UB / RT** | Lower band / upper band / return targets from prior sessions. | Labels only. |
| **ZG (Zero Gamma), "the hinge"** | Where dealers flip from long to short gamma. Sets the day's "weather". | Label only. |
| **Major positive / major negative** (vol or OI book) | Largest positive / negative gamma strikes. Positive = defended; negative = accelerant. | Labels only. |
| **Long / short / call / put gamma ("state" strikes)** | Where dealers are longest/shortest gamma now. | Labels only. |
| **Max-change strike** | Where gamma is being added fastest; late in the day, the likely pin. | Label only. |
| **Net GEX, DEX, P/C OI, P/C GEX, Expiring %, EM %, Gamma Condition, Q-Score** | Daily summary statistics. | Plan-wide `other_conditions` if written; never an entry or a level. |

## 4. Structures (how levels combine)

These describe a zone. None of them is an entry by itself.

| Word | Meaning | Store as |
|---|---|---|
| **Weld (★)** | Two or more independent sources on the same price. The strongest levels. | Part of the zone's label. |
| **Stack** | The count of measurable structures at a level. "The stack is the default, the lean is the challenger, the audit is the judge." | Part of the label. |
| **Cluster / magnet** | Many structures in a tight range. Price gravitates there; no trades inside it. | Plan-wide `other_conditions` (the no-trade range, with its prices). |
| **Platform / polarity line** | A former ceiling price now trades above (or floor below). Held retest = continuation; failed retest = trap. | The entry written on it, if any; else `other_conditions`. |
| **Exam** | A big overhead structure (often the hinge) price must pass to change regime. | Label only. |
| **Shelf / floor with memory** | Support already tested, or broken and reclaimed. A broken-and-reclaimed floor is a PIVOT. | The entry written on it, if any. |
| **POC / VAH / VAL / dPOC / value area** | Volume-profile levels (dPOC = today's developing POC). | Labels only. |
| **Home / the open area** | Where price currently sits; no edge there ("no trades at home"). | As a target: price empty, words kept, unless the same gameplan prices it. The rule itself goes to `other_conditions`. |

## 5. Scenario cards (A / B / C)

| Word | Meaning | Store as |
|---|---|---|
| **Scenario A / B / C with weights** | Pre-written if-then plans, not predictions. | `scenarios`: id, prob, bias, desc. Label each set `ny_am`, `session` or `unspecified` as written. Each set sums to 100%. |
| **ARM** | The scenario activates only when price crosses its trigger on size (a real-volume bar). | Scenario `desc`, word for word. |
| **CONFIRM** | Several closes beyond the trigger with volume building. | Scenario `desc`. |
| **Checkpoints / look-for chain / runway** | The levels the story should hit next, in order; runway = open space to the next structure. | Scenario `desc`. |
| **INVALIDATED** | Acceptance back through the story's kill level: the card is dead. | Scenario `desc`. |
| **ONE-LINER** | The whole plan in one sentence. | Plan-wide `other_conditions`, word for word. |

**A scenario card never creates an entry by itself, and its conditions stay on the scenario.**
Attach a scenario's condition, trigger or invalidation to an entry only when the text names
that entry (its ID) or writes the condition on the entry's own line.

## 6. Context gauges and rules of engagement

All of these go into `other_conditions` word for word (on the entry they are written under,
otherwise plan-wide). No feature reads them yet.

- **VIX gates:** preset VIX lines (first gate: longs only from floor audits; regime line ~19.5: no longs). VIX on/below its own Put Support = complacency.
- **10Y yield gate** (e.g. 4.75/4.80): yields breaking up = hostile for longs ("the hostile double" with VIX rising); yields falling with stocks falling = growth scare. "Sponsors" (VIX/10Y sponsors): see section 8.
- **Dealer migration**, **pin** (late-day pull to the dominant 0DTE strike on heavy-expiry days).
- **Quarter size**, **audits only**, **no trades at home**, **event law** (nothing held into a scheduled print), **stand-down windows** (heavy-expiry afternoons after ~2:00, the last minutes into the bell), **INVALID means stop**.

The bot has its own news blackout (§9) and setup windows (§1.2); the gameplan's event and
stand-down rules are stored but never replace them.

**ES alignment:** NQ trades the levels; ES confirms. An ES level written as a condition of a
named entry is that entry's ES gate. Always keep the gate's words. Explicit words set the
direction (above, over, > / below, under, <). Per F8 (owner, §13): "holding" and "reclaiming"
X read as ES above X; "losing", "rejecting" and "failing" X read as ES below X; anything else
("stalls under", "sits on") stays unclear.

## 7. Writing conventions and owner rules

- **Dates:** use the gameplan's DATE line or the owner-confirmed date. Never infer a year.
- **Two versions of one value** (header vs bullets, trade line vs narrative): use the version marked FINAL. If none is marked FINAL, leave the value empty and flag it for review. **Never pick one.**
- **Conditional entries** ("acceptance above X", breakout, breakdown, break-plan, a branch written on an entry line with a direction and targets): the zone stays **empty**, the level goes in `trigger.nq_price`, the condition goes word for word in the trigger note, and stop and targets are kept. Never use the level's range as the zone. Side = the direction of the break.
- **Break vs retest (owner 2026-10-07):** an entry that trades the break itself ("acceptance below X", breakout, breakdown, lost, reclaimed, initiation) has an **empty zone** and the level in `trigger.nq_price`. An entry that trades the **retest of a written zone after a break** (failed reclaim, held retest, audit) **keeps that zone**, with trigger type `failed_audit` (sell) or `audited_hold` (buy) and the break as its precondition, word for word, in the trigger note (e.g. 09-10 E5: "failed reclaim of 29,000–026 after a settled bar under 29,026" keeps the zone 29,000–29,026).
- **Ranges and sequences:** "–" joins the two ends of one range. "→" and "then" separate targets in order. A slash pair ("30,925 / 30,884", "30,133/30,157") is **two separate targets, never a range**: the nearer one is the conservative target, the farther one the more aggressive target. Store them as two targets, nearer first.
- **Named targets:** a price only if the same gameplan gives that name a price; otherwise the price stays empty and the words are kept.
- **Stops and invalidations inside their zone:** keep as written; they are flagged as info, never changed.
- **Invalidation timeframes:** `1m_close`, `settled_bar` (= the §5.5 settled bar), `acceptance` (minutes as written, else 15), `not_stated`, `other`.
- **The model's own notes:** label each `info` or `review`. Info = it only says something is missing, or it is covered by a rule above (dates the owner confirmed, stops kept as written, the FINAL version, contradictions left empty). Everything else is review.

## 8. Terms the cheat sheet doesn't define (owner-defined, from the gameplans)

Until a term has a meaning here, keep its words and don't map it to a field or a price.
Examples are from the 10-05 gameplan.

| Term | Meaning (owner) |
|---|---|
| **lid** | The SELL zone capping the current range from above ("the lid 31,088–31,165"). "Lost the lid" = price fell back below it after trading above. A zone label. |
| **floor** | The BUY zone under the current range ("the floor 30,954–31,010"). A zone label. |
| **floor-break / "the floor's break"** | Acceptance below the floor zone: a conditional SELL (zone empty, level = the floor's lower edge). |
| **rung** | One step (zone) of the ladder table ("the discovery rung 31,200–31,300", "the old upper rung 30,848–30,925"). "The rung underside" = that rung's lower edge, priced only if the rung's range is written in the same gameplan. |
| **discovery** | Trading above the recent highs into new territory. "The discovery rung" = the zone at the prior highs; "discovery X → Y" = the target path once price is above them. |
| **sponsors** | A VIX or 10Y condition that supports a move; never the direction itself ("Over 16.63 sponsors the floor's break"; "The 10Y sponsors a move; it is never the direction"). `other_conditions` on the entry it is written for. |
| **lost / reclaimed / held** (as triggers) | "Lost X" / "lost under X" = acceptance below X: a conditional SELL ("Lost under 30,848 with ES under 7,733 → 30,815 then 30,780. Stop 30,873"). "Reclaimed X" = acceptance back above X after being below: a conditional BUY. "Held" = an audited hold (the BUY trigger). |
| **slash pairs** ("30,925 / 30,884", "30,133/30,157") | Two separate targets, never a range: the nearer one is the conservative target, the farther one the more aggressive target. Stored as two targets, nearer first. |
| **"the HVL pair at X"**, **"HVL/HVL0 X"**, **"HVL & HVL0 X"**, **"HVL = HVL0 X"** | HVL and HVL0 both at X: store X in both `hvl` and `hvl0` (the gameplan states both, so this is not copying). Where only one of them is written ("HVL 29,190"), the other stays empty (owner 2026-10-07). |
| **"the gexbot ceiling"** | The gexbot (live dealer book) strike acting as the ceiling above price. A label, priced only if written. |
| orbit / "orbit resumes" / orbit centre | |
| battleground / "the battleground underside" | |
| signature ("15+ min above with the signature") | |
| unwind / weld-unwind | |
| loss / initiation (as trigger names) | |

## 9. More terms used in the gameplans

| Term | Meaning | Store as |
|---|---|---|
| **NO TRADE** (a ladder row) | A range with no entries ("31,010–31,088 NO TRADE"). | Not an entry. Plan-wide `other_conditions` with its prices. Time windows like "nothing from 09:58 to 10:03" also go there (the bot's own news rule, §9, is separate). |
| **Reversion / Breakout** (the two branches under each key level) | Reversion (marked "the default") is the main trade at that zone. Breakout ("acceptance higher/lower", "a settled bar over/under X with ES over/under Y") is the trade if the zone breaks. | Reversion: the zone's entry, with its own targets and stop. Breakout: a conditional entry in the break's direction (zone empty, level in `trigger.nq_price`), with its own targets and stop. |
| **"Stop X by touch"** | The stop is hit when price trades at X, not on a close. | Stop X; keep "by touch" in the entry's note. |
| **the release** | The day's scheduled data release ("The release is the scheduled trigger"). | Not a trigger type. `other_conditions`. |
| **ES twin** | The ES levels matching an NQ zone (the overlaps table). | Context only. ES gates come from the ladder's "ES gate" column or the entry's own line. |
| **+VOL / −VOL / +OI / −OI** | The major positive / negative strikes of the volume and open-interest books. | Labels only. |
| **long-g / short-g / call-g / put-g** | The "state" strikes. | Labels only. |
| **CR0 / GW0** | Call Resistance 0DTE / Gamma Wall 0DTE. | Labels only. |
| **card / "the last four cards"** | A previous day's gameplan or dealer book. | Context only. |
| **"lesson L…", "specimens", quant notes, headline risk, the week's calendar** | Notes. | Plan-wide `other_conditions`. |
