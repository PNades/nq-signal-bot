# **THE GAMEPLAN CHEAT SHEET — every term on the map, in plain language**

*Read this once and the daily gameplan reads itself. Levels tell you WHERE; the roles and rules tell you WHAT to do there — and most of the time the answer is "wait for the audit."*

---

## **1\. THE ROLES (the word at the start of every line)**

* **BUY** — a zone where we will only buy an *audited hold* (see §4). Never a "touch it, buy it" instruction.  
* **SELL** — a zone where we will only sell a *failed auction / failed audit*. Same discipline, opposite side.  
* **PIVOT** — a two-way level: the market rotates around it. No standing trade; watch which side accepts.  
* **TARGET** — where a move is aiming. Take-profit territory, not an entry.  
* **INVALID** — if price *accepts* beyond this, today's map is wrong. Stand down, wait for the re-map.  
* **NOTE / OBS** — reference info (usually ES levels used for confirmation). Observe only, never trade off it alone.

## **2\. THE LEVEL SOURCES (where the numbers come from)**

**MenthorQ (the "carried" options book — positions held overnight):**

* **Call Resistance (CR)** — the strike with the heaviest call positioning. Acts as the market's ceiling.  
* **Put Support (PS)** — heaviest put strike. The market's floor of record.  
* **HVL** — the volatility regime line. **Above HVL \= positive-gamma behavior (calm, mean-reverting). Below HVL \= negative-gamma behavior (fast, trending, slippery).** One of the most important lines on the sheet.  
* **1D Min / 1D Max** — the expected-move envelope for the day (roughly the ±1 standard-deviation range).  
* **0DTE levels (CR 0DTE, PS 0DTE, HVL 0DTE, Gamma Wall 0DTE)** — same ideas, but built ONLY from options expiring today. This is where the day-trading option flow planted its flags — often the day's true battleground.  
* **GEX 1–10** — the ten largest gamma strikes, ranked by size (GEX 1 \= biggest). Each is a hedging shelf.  
* **BL 1–10 ("Blind Spots")** — model levels where hedging pressure concentrates; ranked, re-computed daily.  
* **LB / UB / RT** — lower band / upper band / return targets from prior sessions; envelope memory levels.

**Gexbot (the live dealer book, two ledgers):**

* **ZG (Zero Gamma), a.k.a. "the hinge"** — the price where dealers flip from long to short gamma. Above it, dealer hedging dampens moves; below it, hedging amplifies them. The hinge "sets the weather."  
* **Volume book vs OI book (the two-ledger law)** — the VOLUME book is *today's* options activity (governs intraday behavior); the OPEN INTEREST (OI) book is the *carried* position. They can disagree — a "positive gamma" headline can hide a negative day-book. When they agree, believe the agreement.  
* **Major positive / major negative (vol or OI)** — the largest positive- and negative-gamma strikes in each ledger. **Positive strikes are DEFENDED** (dealer hedging pushes price away from them — support below you, resistance above you that becomes support once accepted). **Negative strikes are ACCELERANTS** (hedging pushes price *through* them).  
* **Long / short / call / put gamma ("state" strikes)** — where dealers are longest and shortest gamma right now; put-gamma below price acts like a defended floor, short/call-gamma above like a supply ceiling.  
* **Net GEX** — the book's overall gamma sign and size. **DEX** — net delta exposure (directional pressure).  
* **Max-change strike** — the strike where gamma is being ADDED fastest right now. Late in the day it's the best guess at where price pins.

**(daily summary stats):** P/C OI (put/call open-interest ratio) · P/C GEX (put/call gamma ratio — above 1 \= put-heavy book) · Expiring % (how much of the book dies today — big % \= afternoon pin risk and a rebuilt map tomorrow) · EM % (expected move) · Gamma Condition (the headline sign) · Q-Score (quant flags).

## **3\. THE STRUCTURES (words we use for how levels combine)**

* **Weld (★)** — two or more INDEPENDENT sources landing on the same price (e.g., a gexbot major \+ a MenthorQ BL \+ a weekly POC). Welds are the strongest levels on the map — measured, not opinion.  
* **Stack** — the count of measurable structures at/around a level (value-area edge, majors, ES alignment, flow). **"The stack is the default, the lean is the challenger, the audit is the judge."** A signal firing INTO a 3-deep stack is a break-plan candidate, not a trade, until the audit confirms.  
* **Cluster / magnet** — many structures packed in a tight range. Price gravitates to it; no trades inside it.  
* **Platform / polarity line** — a former ceiling that price now trades ABOVE (or floor below). Held retest from the new side \= continuation; failed retest \= trap. The retest is everything.  
* **Exam** — a big overhead structure (often the hinge) that price must pass to change regime.  
* **Shelf / floor with memory** — support that has already been tested (or broken and reclaimed). A broken- and-reclaimed floor is a PIVOT, not a virgin defense — respect its memory.  
* **POC / VAH / VAL / value area** — volume-profile terms: point of control (most-traded price) and the value-area high/low (where \~70% of business happened). dPOC \= today's developing POC. "Price below both VALs" \= trading under both this week's and last week's accepted value.

## **4\. THE VERBS (what price does at a level — this decides everything)**

* **Test / touch** — price reaches the level. By itself this means NOTHING tradeable.  
* **Acceptance** — price *stays* beyond a level (multiple closes, usually 10–15 min) and builds volume there. Acceptance is how a level actually breaks.  
* **Failed auction / rejection (REVERSION)** — price pokes beyond, finds no business, and comes back fast. The move was a probe, not a break.  
* **THE AUDIT (our most important word)** — the RETEST after a break. Price breaks a level, comes back to test it from the other side: if the level HOLDS the retest, the break is real (that's an *audited hold* — our buy trigger). If the retest FAILS, the break was false (a *failed audit* — our sell trigger). **We trade the audit, never the first touch.**  
* **Break-plan** — a level tested three times stops being a fade and becomes a breakout candidate (third test \= plan the break, don't reflexively fade it).  
* **Reversion vs breakout at any level:** REVERSION looks like thinning volume into the level, a fast reclaim, and dealer positioning NOT chasing. BREAKOUT looks like expanding volume THROUGH the level, a QUIET retest from the far side, and the majors migrating with price. Loud break \+ loud retest that recovers \= the trap.

## **5\. THE RULES OF ENGAGEMENT (the discipline layer)**

* **Quarter size** — reduced size, always, in fast/negative-gamma regimes and around events. Confluence upgrades confidence in the LEVEL, never the size.  
* **Audits only** — no entry without the retest evidence (§4). "The grade card measures location; the audit measures the trade."  
* **No trades at home** — where price currently sits ("the open area") offers no edge.  
* **Event law** — nothing held into a scheduled print (CPI/ISM/ADP/NFP etc.). The print gets a clean book.  
* **Stand-down windows** — heavy-expiry afternoons (after \~2:00) and the last minutes into the bell: no new unaudited positions.  
* **INVALID means stop** — acceptance beyond an INVALID line \= the map is wrong. No hero trades; re-map.

## **6\. THE CONTEXT GAUGES**

* **VIX gates** — pre-set VIX lines: through the first gate, longs get de-weighted to floor-audits only; through the regime line (\~19.5), stand down from longs entirely. VIX sitting on/below its own Put Support \= complacency loaded (quiet ≠ safe).  
* **10Y yield** — the rate gate (e.g., 4.75/4.80): yields breaking UP \= hostile for longs ("the hostile double" when VIX rises too); yields falling \= fuel for upside. Special case: yields falling WITH stocks falling \= growth scare — downside accelerates without the usual warning.  
* **ES alignment** — NQ trades the levels; ES confirms. Divergence is information: NQ holding its level while ES breaks (or vice versa) tells you which index is being defended and which move is unsupported.  
* **Dealer migration** — do the major strikes MOVE with price (repositioning, trend-friendly) or sit still while price approaches (the wall is waiting — fade material)? Where gamma is being added intraday is where the new floor/lid is materializing.  
* **Pin** — on heavy-expiry days, price late-day gravitates to the dominant 0DTE strike (watch the max-change leader after noon).

## **7\. THE SCENARIO CARDS (A / B / C in every gameplan)**

Each frame ships 2–4 scenario stories with weights (e.g., A 35% / B 35% / C 30%) — not predictions, *pre-written if-then plans* so nothing that happens is a surprise:

* **ARM** — the scenario activates only when price crosses its trigger level ON SIZE (a real-volume bar, not a drift). A cross without volume is refused — "a cross is a claim; size is the qualifier."  
* **CONFIRM** — several closes beyond the trigger with volume building \= the story is live.  
* **CHECKPOINTS / look-for chain** — the levels the story should hit next, in order. **Runway** \= points of open space to the next structure.  
* **INVALIDATED** — acceptance back through the story's kill level \= the card is dead (terminal — a new story requires a new card).  
* The **ONE-LINER** at the bottom of every frame is the whole plan compressed into one panic-proof sentence. If the day gets fast, trade the one-liner and nothing else.

---

