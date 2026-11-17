// Review (SPEC §8, §9b item 5; design-system/pages/review.md): one setup card at a time. The main chart follows a replay
// clock (Restart / Play / Minute → / Next card, speed, slider) that stops at the decision time until the owner answers
// (§8: the outcome stays hidden, enforced by the server: the card's later half isn't sent before the answer); the
// "at this point" panel beside it holds the card's facts and the answer (Take T / Skip S, the reason, optional entry /
// stop / target / confidence, Ctrl+Enter), clear of the dock; the GEX radial for the clock's minute and the detector
// readings follow below. After the answer the result card drops in and the rest of the path plays out on the chart.
// Back and Later are always there.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ChevronDown, ChevronLeft, ChevronRight, ChevronsRight, CircleCheck, CircleX, Pause, Play, RotateCcw, SkipForward, StepBack, StepForward } from "lucide-react";
import { ApiError, postJson } from "@/lib/api";
import { load, peek, prime } from "@/lib/prefetch";
import { cn } from "@/lib/utils";
import { useAnimated } from "@/effects";
import { EmptyState, PageHeader, Skeleton } from "@/shell/page";
import { pageById } from "@/shell/pages";
import { BAR_BOTTOM, DOCK_PAD, useDockLayout } from "@/shell/Shell";
import { FlipDiskDisplay } from "@/components/ui/flip-disk-matrix";
import { Kbd } from "@/components/ui/kbd";
import { notify } from "@/lib/notify";
import { MainChart, type ChartView, type LayerId } from "@/chart/MainChart";
import { GexRadial } from "@/chart/GexRadial";
import { clockOf, joined, lastAt, MAP_ROOT, segAt } from "@/chart/data";
import type { TradeLines } from "@/chart/overlay";
import { price as fmtPrice, rr } from "@/lib/format";
import type { Answer, Card, MapName, ReviewIndex, RoundCard } from "./types";
import { cardUrl, EMPTY_DRAFT, pickCard, readDraft, reviewMemory, reviewUrl, writeDraft, type Draft } from "./query";

const SPEEDS = [1, 2, 4] as const;
const STEP_MS = 650;                    // one replay minute at 1×
const REVEAL_STEP_MS = 90;              // after the answer the rest of the path plays out at this pace (Full)
const MAPS: MapName[] = ["QQQ", "NDXP", "SPY", "SPX"];
// the plot takes the window's height left between its top and the dock (open or minimized), so the replay bar stays
// clear of the dock when the page opens; never under PLOT_MIN or over PLOT_MAX (full screen: F). At 1280 px and wider the
// chart card also stretches to the height of the column beside it, so no gap is left under it (owner 2026-11-16); the
// replay bar is at the chart's bottom (round 3: the dock may cover it; scroll or minimize the dock)
const PLOT_MIN = 300, PLOT_MAX = 760, REPLAY_H = 49, TOOLBAR_H = 45;
const ENTRY_CHOICES = [["order_flow", "Order-flow trigger"], ["audit", "Audit"], ["plain", "Plain at the level"]] as const;
const STYLE_NAME: Record<string, string> = { c: "(c) audit / sweep reclaim", d: "(d) plain at the touch", a: "(a) absorption", b: "(b) order flow" };
const ORDER_NAME: Record<string, string> = { limit: "limit", stop_limit: "stop-limit", marketable_limit: "marketable limit", limit_center: "limit at the centre" };
const RESULT_NAME: Record<string, string> = { target: "Take profit", stop: "Stop loss", timeout: "Timeout" };

function typing(el: EventTarget | null) {
  const t = el as HTMLElement | null;
  return !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
}

export function ReviewPage() {
  const animated = useAnimated();
  const page = pageById("review");
  const [idx, setIdx] = useState<ReviewIndex | null>(() => peek<ReviewIndex>(reviewUrl(reviewMemory.round)) ?? null);
  const [cardId, setCardId] = useState<string | null>(() => (idx ? pickCard(idx) : null));
  const roundId = idx?.round?.id ?? null;
  const [card, setCard] = useState<Card | null>(() => (roundId && cardId ? peek<Card>(cardUrl(roundId, cardId)) ?? null : null));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let off = false;
    load<ReviewIndex>(reviewUrl(reviewMemory.round)).then((d) => { if (!off) { setIdx(d); setCardId((c) => c ?? pickCard(d)); } })
      .catch((e: unknown) => { if (!off) setError(e instanceof Error ? e.message : String(e)); });
    return () => { off = true; };
  }, []);
  useEffect(() => {
    if (!roundId || !cardId) return;
    reviewMemory.round = roundId; reviewMemory.card = cardId;
    let off = false;
    load<Card>(cardUrl(roundId, cardId)).then((c) => { if (!off) setCard(c); })
      .catch((e: unknown) => { if (!off) setError(e instanceof Error ? e.message : String(e)); });
    return () => { off = true; };
  }, [roundId, cardId]);

  const cards = idx?.round?.cards ?? [];
  const pos = cards.findIndex((c) => c.id === cardId);
  const go = useCallback((delta: number, unansweredOnly = false) => {
    if (!cards.length) return;
    for (let k = 1; k <= cards.length; k++) {
      const c = cards[(pos + delta * k + cards.length * 4) % cards.length];
      if (!unansweredOnly || !c.answered) { setCardId(c.id); return; }
    }
  }, [cards, pos]);

  const onAnswered = (c: Card, a: Answer) => {
    setCard(c);
    if (roundId) prime(cardUrl(roundId, c.id), c);
    setIdx((d) => d && d.round ? { ...d, round: { ...d.round, answered: d.round.answered + 1, correct: d.round.correct + (a.correct ? 1 : 0),
      cards: d.round.cards.map((x) => x.id === c.id ? { ...x, answered: true, verdict: a.verdict, correct: a.correct, stratum: c.stratum } : x) } } : d);
  };

  if (error) return <Frame><PageHeader page={page} /><EmptyState word="Review" message={`The review data couldn't be read: ${error}`} /></Frame>;
  if (!idx) return <Frame><PageHeader page={page} /><Skeleton className="h-[640px] w-full rounded-md" /></Frame>;
  if (!idx.round) return <Frame><PageHeader page={page} /><EmptyState word="Review" message={idx.empty ?? "No review round yet."} /></Frame>;
  const r = idx.round;
  return (
    <Frame>
      <PageHeader page={page} right={<Progress answered={r.answered} n={r.n} correct={r.correct} animated={animated} />} />
      <CardStrip cards={cards} current={cardId} onPick={setCardId} onBack={() => go(-1)} onLater={() => go(1, true)} onNext={() => go(1)}
        info={<>
          {r.synthetic && <span className="rounded-xs bg-warn px-1.5 py-0.5 font-mono text-[11px] font-semibold text-bg-0">SYNTHETIC DATA</span>}
          {r.preview && <span className="rounded-xs border border-info/60 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-info"
            data-hint="A preview round: its answers are kept apart from the real ones, and every touch its cards show is left out of the real rounds">PREVIEW</span>}
          <span className="whitespace-nowrap rounded-full border border-line-strong px-2 py-0.5 font-mono" data-hint={`build ${r.build_key} · labels ${r.labels_key}${r.months ? ` · months ${r.months.join(", ")}` : ""}`}>round {r.id}</span>
        </>} />
      {card && card.id === cardId ? <CardView key={card.id} round={r.id} card={card} animated={animated} onAnswered={onAnswered} onNext={() => go(1)} onLater={() => go(1, true)} onPrev={() => go(-1)} />
        : <Skeleton className="h-[640px] w-full rounded-md" />}
    </Frame>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto flex max-w-[1680px] flex-col gap-4 px-6 pb-0 pt-7 min-[1440px]:px-8" data-page="review">{children}</div>;
}

function Progress({ answered, n, correct, animated }: { answered: number; n: number; correct: number; animated: boolean }) {
  const text = `${String(answered).padStart(2, "0")}/${n}`;
  return (
    <div className="flex items-center gap-3" data-progress>
      <div className="text-right">
        <div className="eyebrow">Answered</div>
        <div className="mt-1 font-mono text-[12px] text-text-2">{correct} agreed with the outcome</div>
      </div>
      <FlipDiskDisplay text={text} rows={9} animated={animated} className="w-[170px]" label={`${answered} of ${n} cards answered, ${correct} agreed with the outcome`} />
    </div>
  );
}

function CardStrip({ cards, current, onPick, onBack, onLater, onNext, info }: {
  cards: RoundCard[];
  current: string | null; onPick: (id: string) => void; onBack: () => void; onLater: () => void; onNext: () => void; info: React.ReactNode;
}) {
  const row = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });
  const measure = useCallback(() => {
    const el = row.current;
    if (el) setEdges({ left: el.scrollLeft > 1, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 1 });
  }, []);
  // the current card stays in view in the one-row strip (scrolled within the strip only, never the page)
  useEffect(() => {
    const el = row.current, cur = el?.querySelector<HTMLElement>("[aria-current=true]");
    if (el && cur) {
      const l = cur.offsetLeft - el.offsetLeft, r = l + cur.offsetWidth;
      if (l < el.scrollLeft + 28) el.scrollTo({ left: Math.max(0, l - 28) });
      else if (r > el.scrollLeft + el.clientWidth - 28) el.scrollTo({ left: r - el.clientWidth + 28 });
    }
    measure();
  }, [current, cards.length, measure]);
  useEffect(() => {
    const el = row.current;
    if (!el) return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [measure]);
  const page = (d: number) => { const el = row.current; if (el) el.scrollBy({ left: d * el.clientWidth * 0.8, behavior: "smooth" }); };
  const fade = `linear-gradient(to right, ${edges.left ? "transparent, #000 28px" : "#000, #000"}, ${edges.right ? "#000 calc(100% - 28px), transparent" : "#000, #000"})`;
  return (
    <div className="flex items-center gap-3" data-strip>
      <div className="flex shrink-0 items-center gap-2 text-[12px] text-text-2">{info}</div>
      <button type="button" onClick={() => page(-1)} disabled={!edges.left} aria-label="Earlier cards" data-strip-left
        className="inline-flex size-7 shrink-0 items-center justify-center rounded-full text-text-2 hover:bg-bg-3 hover:text-text-1 disabled:opacity-30"><ChevronLeft className="size-4" aria-hidden="true" /></button>
      <div ref={row} onScroll={measure} className="cf-noscrollbar flex min-w-0 flex-1 gap-1 overflow-x-auto py-1" role="list" aria-label="Cards"
        style={{ maskImage: fade, WebkitMaskImage: fade }} data-strip-row>
        {cards.map((c) => (
          <button key={c.id} type="button" role="listitem" onClick={() => onPick(c.id)} aria-current={c.id === current ? "true" : undefined}
            data-hint={c.answered ? `${c.id}: ${c.verdict}${c.correct == null ? "" : c.correct ? ", agreed with the outcome" : ", against the outcome"}` : `${c.id}: not answered yet`}
            className={cn("relative size-6 shrink-0 rounded-xs border font-mono text-[10px] leading-none transition-colors",
              c.id === current ? "border-cream bg-caramel text-cream" : c.answered ? "border-line-strong bg-bg-2 text-text-2" : "border-line bg-bg-1 text-text-3 hover:border-line-strong hover:text-text-1")}>
            {c.id.slice(1)}
            {c.answered && c.correct != null && <span className={cn("absolute -right-0.5 -top-0.5 size-1.5 rounded-full", c.correct ? "bg-ok" : "bg-crit")} aria-hidden="true" />}
          </button>
        ))}
      </div>
      <button type="button" onClick={() => page(1)} disabled={!edges.right} aria-label="Later cards" data-strip-right
        className="inline-flex size-7 shrink-0 items-center justify-center rounded-full text-text-2 hover:bg-bg-3 hover:text-text-1 disabled:opacity-30"><ChevronRight className="size-4" aria-hidden="true" /></button>
      <div className="flex shrink-0 items-center gap-1.5">
        <NavBtn onClick={onBack} icon={<ArrowLeft className="size-4" />} label="Back" keys={["P"]} />
        <NavBtn onClick={onLater} icon={<SkipForward className="size-4" />} label="Later" keys={["L"]} hint="Leave this card for later: the next card not answered yet" />
        <NavBtn onClick={onNext} icon={<ArrowRight className="size-4" />} label="Next card" keys={["N"]} />
      </div>
    </div>
  );
}

function NavBtn({ onClick, icon, label, keys, hint }: { onClick: () => void; icon: React.ReactNode; label: string; keys: string[]; hint?: string }) {
  return (
    <button type="button" onClick={onClick} data-hint={hint}
      className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line-strong bg-bg-2 px-3 text-[13px] text-text-1 hover:border-text-3">
      <span aria-hidden="true">{icon}</span>{label}<Kbd keys={keys} className="ml-0.5" />
    </button>
  );
}

// ── one card ──────────────────────────────────────────────────────────────────────────────────────────────────────

function CardView({ round, card, animated, onAnswered, onNext, onLater, onPrev }: {
  round: string; card: Card; animated: boolean; onAnswered: (c: Card, a: Answer) => void; onNext: () => void; onLater: () => void; onPrev: () => void;
}) {
  const half = useMemo(() => joined(card), [card]);
  const answered = card.answer != null && card.post != null;
  const tMin = card.t.minute;
  const maxClock = answered ? card.t.reveal_end - (card.t.reveal_end % 60 === 0 ? 60 : card.t.reveal_end % 60) : tMin;
  const [clock, setClock] = useState(tMin);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const [inst, setInst] = useState<"NQ" | "ES">("NQ");
  const [layers, setLayers] = useState<Record<LayerId, boolean>>({ zones: true, lines: true, highs: true, nodes: true, touches: true });
  // the chart (Focus) or the table, exactly one; the chart when a card opens (owner 2026-11-16)
  const [view, setView] = useState<ChartView>("focus");
  // A: every level at full strength (the old Focus-off view, kept until the owner decides whether it goes)
  const [allLevels, setAllLevels] = useState(false);
  const [map, setMap] = useState<MapName>("QQQ");
  const limit = answered ? card.t.reveal_end : card.t.decision;

  // the replay clock
  const timer = useRef<number | null>(null);
  useEffect(() => {
    if (!playing) return;
    const ms = answered && clock >= tMin ? (animated ? REVEAL_STEP_MS : 0) : STEP_MS / speed;
    timer.current = window.setTimeout(() => {
      setClock((c) => {
        const n = ms === 0 ? maxClock : Math.min(maxClock, c + 60);
        if (n >= maxClock) setPlaying(false);
        return n;
      });
    }, ms);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [playing, clock, speed, maxClock, answered, tMin, animated]);
  const rStart = replayStart(card);
  const restart = () => { setPlaying(false); setClock(rStart); };
  const play = () => { if (clock >= maxClock) setClock(rStart); setPlaying((p) => !p); };
  const step = (d: number) => { setPlaying(false); setClock((c) => Math.max(rStart, Math.min(maxClock, c + 60 * d))); };
  // the end of what may be shown: the decision before the answer, the end of the revealed path after it
  const toEnd = () => { setPlaying(false); setClock(maxClock); };
  // after the answer the rest of the path plays out from the decision (Calm: at once)
  const wasAnswered = useRef(answered);
  useEffect(() => {
    if (answered && !wasAnswered.current) { setClock(tMin); setPlaying(true); }
    wasAnswered.current = answered;
  }, [answered, tMin]);

  const trade: TradeLines = useMemo(() => ({
    from: card.t.decision, entry: card.trade.entry, stop: card.trade.stop, target: card.trade.target, dir: card.setup.direction,
    fill: answered && card.outcome?.t_fill != null && card.outcome.fill != null ? { t: card.outcome.t_fill, px: card.outcome.fill } : null,
    exit: answered && card.outcome?.t_exit != null && card.outcome.exit != null ? { t: card.outcome.t_exit, px: card.outcome.exit, result: card.outcome.result } : null,
  }), [card, answered]);

  const chartBox = useRef<HTMLElement>(null);
  const { barBottom } = useDockLayout();
  const [plotH, setPlotH] = useState(420);
  useLayoutEffect(() => {
    const fit = () => {
      const el = chartBox.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top + (document.getElementById("cf-main")?.scrollTop ?? 0);
      const pad = barBottom === BAR_BOTTOM.low ? DOCK_PAD.min : DOCK_PAD.open;
      setPlotH(Math.round(Math.max(PLOT_MIN, Math.min(PLOT_MAX, window.innerHeight - top - TOOLBAR_H - REPLAY_H - pad - 8))));
      void REPLAY_H;
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [barBottom]);
  // the setup's own levels (stacked in its zone at the decision) and the target's level: drawn at full strength
  const focusUids = useMemo(() => {
    const z = card.setup.zone, tg = card.trade.target, out = new Set<string>();
    for (const l of card.pre.levels.NQ) {
      const b = segAt(l, card.t.minute);
      if (!b) continue;
      const c = (b[0] + b[1]) / 2;
      if ((c >= z.lo - 0.01 && c <= z.hi + 0.01) || (tg >= b[0] - 1 && tg <= b[1] + 1)) out.add(l.uid);
    }
    return out;
  }, [card]);
  const snap = useMemo(() => lastAt(half.maps[map] ?? [], clock, (s) => s.t), [half, map, clock]);
  const relevant = useMemo(() => {
    const set = new Set<number>();
    for (const l of half.levels[MAP_ROOT[map]]) if (l.node && l.chain === map && l.strike != null && segAt(l, clock)) set.add(l.strike);
    return set;
  }, [half, map, clock]);

  // keys (pages/review.md): Space play / pause, ← → a minute, Home restart, F full screen, N / L / P cards
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || document.querySelector("[role=dialog]")) return;
      const k = e.key;
      if (k === " ") { e.preventDefault(); play(); }
      else if (k === "ArrowRight") { e.preventDefault(); step(1); }
      else if (k === "ArrowLeft") { e.preventDefault(); step(-1); }
      else if (k === "Home") { e.preventDefault(); restart(); }
      else if (k === "End") { e.preventDefault(); toEnd(); }
      else if (k === "a" || k === "A") { e.preventDefault(); setAllLevels((v) => !v); }
      else if (k === "f" || k === "F") { e.preventDefault(); document.getElementById("cf-main-chart")?.requestFullscreen?.(); }
      else if (k === "n" || k === "N") { e.preventDefault(); onNext(); }
      else if (k === "l" || k === "L") { e.preventDefault(); onLater(); }
      else if (k === "p" || k === "P") { e.preventDefault(); onPrev(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const off = card.et_offset_sec;
  return (
    <div className="flex flex-col gap-4" data-card={card.id}>
      <div className="grid gap-4 min-[1280px]:grid-cols-[minmax(0,1fr)_360px]">
        <section ref={chartBox} className="flex flex-col overflow-hidden rounded-md border border-line bg-bg-1" aria-label="Main chart" data-chart-card>
          <MainChart half={half} inst={inst} off={off} clock={clock} limit={limit} trade={trade} layers={layers}
            onLayer={(id) => setLayers((l) => ({ ...l, [id]: !l[id] }))} onInst={setInst} view={view} onView={setView}
            focus={!allLevels} focusUids={focusUids} ownZone={card.setup.zone.id} minHeight={plotH}
            replay={<ReplayBar card={card} clock={clock} max={maxClock} playing={playing} speed={speed} answered={answered}
              onRestart={restart} onPlay={play} onStep={step} onEnd={toEnd} onSpeed={setSpeed} onSeek={(t) => { setPlaying(false); setClock(t); }} />} />
        </section>
        <aside className="flex flex-col gap-4 self-start" aria-label="At this point">
          <Facts card={card} clock={clock} off={off} />
          <AnimatePresence mode="wait" initial={false}>
            {answered
              ? <ResultCard key="result" card={card} animated={animated} onNext={onLater} />
              : <AnswerPanel key="answer" round={round} card={card} animated={animated} onAnswered={onAnswered} />}
          </AnimatePresence>
        </aside>
      </div>
      <div className="grid gap-4 min-[1280px]:grid-cols-[400px_minmax(0,1fr)]">
        <section className="rounded-md border border-line bg-bg-1 p-4" aria-label="GEX snapshot">
          <GexRadial maps={MAPS} map={map} onMap={setMap} snap={snap} relevant={relevant} unit={MAP_ROOT[map]} animated={animated} />
        </section>
        <Readings card={card} />
      </div>
    </div>
  );
}

/** the replay window starts REPLAY_WINDOW_MIN minutes before the decision's minute (never before the chart's start);
 * computed here rather than read from `t.replay_start`, which older cards stored as 45 min, clipped at the open */
const REPLAY_WINDOW_MIN = 30;
function replayStart(card: Card) {
  return Math.max(card.t.chart_start, card.t.decision - (card.t.decision % 60) - REPLAY_WINDOW_MIN * 60);
}

function ReplayBar({ card, clock, max, playing, speed, answered, onRestart, onPlay, onStep, onEnd, onSpeed, onSeek }: {
  card: Card; clock: number; max: number; playing: boolean; speed: number; answered: boolean;
  onRestart: () => void; onPlay: () => void; onStep: (d: number) => void; onEnd: () => void; onSpeed: (s: (typeof SPEEDS)[number]) => void; onSeek: (t: number) => void;
}) {
  // the slider reaches back to the session's open, 18:00 ET the evening before (owner 2026-11-16)
  // round 5 (owner 2026-11-16): the slider covers the 30 minutes before the decision (after the answer, through the
  // revealed path too); the chart still shows every candle from 18:00 ET, so the owner can scroll back further
  const off = card.et_offset_sec, start = replayStart(card);
  const span = Math.max(60, max - start);
  const atEnd = clock >= max;
  const pct = (t: number) => `${Math.max(0, Math.min(100, ((t - start) / span) * 100))}%`;
  const cur = Math.max(start, Math.min(max, clock));
  // one row on one centre line (owner 2026-11-16, round 4): the transport, the speed, the scrubber, the clock. The
  // scrubber is our own track, fill and thumb over a transparent native range input (which keeps the keys and the
  // screen reader), in the pattern of the playback scrubbers looked at on 21st.dev (Playback Scrubber 35685, the
  // Video Player 30231 transport); ticks mark the RTH open and, after the answer, the decision
  return (
    <div className="flex h-14 shrink-0 items-center gap-3 border-t border-line px-3" data-replay>
      <div className="flex shrink-0 items-center gap-0.5">
        <IconBtn label={`Restart (${clockOf(start, off)})`} keys="Home" onClick={onRestart}><RotateCcw className="size-4" /></IconBtn>
        <IconBtn label="A minute back" keys="←" onClick={() => onStep(-1)}><StepBack className="size-4" /></IconBtn>
        <IconBtn label={playing ? "Pause" : "Play"} keys="Space" onClick={onPlay} primary>{playing ? <Pause className="size-4" /> : <Play className="size-4 translate-x-px" />}</IconBtn>
        <IconBtn label="Minute →" keys="→" onClick={() => onStep(1)}><StepForward className="size-4" /></IconBtn>
        <IconBtn label={answered ? "To the end of the revealed path" : "To the decision"} keys="End" onClick={onEnd}><ChevronsRight className="size-4" /></IconBtn>
      </div>
      <div className="flex h-8 shrink-0 items-center rounded-full border border-line-strong p-0.5" role="radiogroup" aria-label="Speed">
        {SPEEDS.map((s) => (
          <button key={s} type="button" role="radio" aria-checked={speed === s} onClick={() => onSpeed(s)}
            className={cn("h-6 rounded-full px-2 font-mono text-[11px] leading-none", speed === s ? "bg-caramel text-cream" : "text-text-2 hover:text-text-1")}>{s}×</button>
        ))}
      </div>
      <div className="relative flex h-8 min-w-[160px] flex-1 items-center" data-scrub-wrap>
        <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-bg-3" aria-hidden="true" />
        <div className="absolute left-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-cream/60" style={{ width: pct(cur) }} aria-hidden="true" />
        {card.t.open > start && card.t.open < max && (
          <span className="absolute top-1/2 h-2.5 w-px -translate-y-1/2 bg-text-3/70" style={{ left: pct(card.t.open) }} data-hint="09:30 ET, the RTH open" aria-hidden="true" />
        )}
        {answered && <span className="absolute top-1/2 h-3 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-cream" style={{ left: pct(card.t.minute) }} aria-hidden="true" />}
        <input type="range" min={start} max={max} step={60} value={cur} onChange={(e) => onSeek(Number(e.target.value))}
          aria-label="Replay clock" aria-valuetext={`${clockOf(clock, off)} ET`} className="peer absolute inset-0 h-full w-full cursor-pointer opacity-0" data-scrub />
        <span className="pointer-events-none absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bg-1 bg-cream shadow-[0_0_0_1px_rgba(255,224,194,.35)] peer-hover:scale-110 peer-focus-visible:ring-2 peer-focus-visible:ring-cream/60"
          style={{ left: pct(cur) }} aria-hidden="true" data-thumb />
      </div>
      {/* round 5 (owner 2026-11-16): the same two lines at all times in a block of fixed width (the scrubber's length
          never changes); at the end of the replay only the decision line's colour turns cream */}
      <div className="flex w-32 shrink-0 flex-col items-end justify-center font-mono leading-tight" data-clock-block>
        <div className="text-[13px] text-text-1" data-clock>{clockOf(clock, off)} ET</div>
        <div className={cn("text-[11px]", atEnd ? "text-cream" : "text-text-3")} data-decision-line>decision {card.t.decision_et}</div>
      </div>
    </div>
  );
}

function IconBtn({ label, keys, onClick, children, primary }: { label: string; keys: string; onClick: () => void; children: React.ReactNode; primary?: boolean }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} data-hint={`${label} (${keys})`}
      className={cn("inline-flex size-8 items-center justify-center rounded-full", primary ? "bg-cream text-bg-0 hover:bg-[#ffe9d4]" : "text-text-2 hover:bg-bg-3 hover:text-text-1")}>
      <span aria-hidden="true">{children}</span>
    </button>
  );
}

function Facts({ card, clock, off }: { card: Card; clock: number; off: number }) {
  const s = card.setup, t = card.trade;
  const long = s.direction > 0;
  const rows: [string, React.ReactNode][] = [
    ["Setup", <>{s.event_type === "golden" ? "Golden zone" : "Level"} touch, {s.role} · <span className={long ? "text-pnl-up" : "text-pnl-down"}>{long ? "long ▲" : "short ▼"}</span></>],
    ["Zone", <span className="font-mono">{fmtPrice(s.zone.lo)} – {fmtPrice(s.zone.hi)}</span>],
    // the same members as the readings' stacked list (the scorer's per-type counts at the decision)
    ["Stacked", card.stack?.items.length ? card.stack.items.map((x) => x.label).join(" · ") : s.members.length ? s.members.join(" · ") : `${s.stack_count} level${s.stack_count === 1 ? "" : "s"}`],
    ["Entry style", STYLE_NAME[t.style] ?? t.style],
    ["Price", <span className="font-mono">{fmtPrice(s.price)}</span>],
  ];
  const trade: [string, React.ReactNode, string?][] = [
    ["Entry", <>{t.entry != null ? fmtPrice(t.entry) : "—"} <span className="text-text-3">{ORDER_NAME[t.order_type] ?? t.order_type}</span></>],
    ["Stop", <>{fmtPrice(t.stop)} <span className="text-text-3">{t.stop_pts.toFixed(2)} pts · {t.stop_rule}</span></>, "text-pnl-down"],
    ["Target", <>{fmtPrice(t.target)} <span className="text-text-3">{t.target_pts.toFixed(2)} pts · {t.target_zone_kind ?? t.target_kind}</span></>, "text-pnl-up"],
    ["Reward : risk", <>{t.net_rr.toFixed(2)} : 1 <span className="text-text-3">net of costs</span></>],
  ];
  return (
    <section className="rounded-md border border-line bg-bg-1 p-4" aria-label="The card's facts" data-facts>
      <div className="flex items-baseline justify-between gap-2">
        <div className="eyebrow">At this point · {clockOf(clock, off)} ET</div>
        <div className="font-mono text-[11px] text-text-3">{card.day} · {card.id}</div>
      </div>
      <dl className="mt-3 grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
        {rows.map(([k, v]) => <Row key={k} k={k} v={v} />)}
      </dl>
      <div className="eyebrow mt-4">The proposed trade</div>
      <dl className="mt-2 grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
        {trade.map(([k, v, cls]) => <Row key={k} k={k} v={<span className={cn("font-mono", cls)}>{v}</span>} />)}
      </dl>
      <p className="mt-3 text-[12px] leading-4 text-text-3">Stop candidate 2 (far edge + buffer), the nearest structural target at 1:1 or more (§6.3 v1); the outcome stays hidden until you answer.</p>
    </section>
  );
}

const ROLE_TEXT: Record<string, string> = { ceiling: "text-pnl-down", floor: "text-pnl-up" };

/** the confluences behind "Levels stacked", as known at the decision time (round 3), shown as a readings group like
 * the others (round 4): the name on the left with a muted note only where it applies (provisional, crossed back,
 * flipped ×n), the price on the right coloured by role */
function Stacked({ stack }: { stack: NonNullable<Card["stack"]> }) {
  return (
    <div className="min-w-0" data-stack>
      <div className="text-[12px] font-medium text-text-2">Stacked ({stack.count})</div>
      <dl className="mt-1.5 flex flex-col gap-1 text-[12px]">
        {stack.items.map((x, i) => {
          const note = x.missing ? "not found" : x.provisional ? (x.crossed_back ? "provisional, crossed back" : "provisional") : x.flips ? `flipped ×${x.flips}` : null;
          return (
            <div key={i} className="flex items-baseline justify-between gap-2" data-stack-item>
              <dt className="min-w-0 truncate text-text-3">{x.label}{note && <span className="text-text-3/70"> · {note}</span>}</dt>
              <dd className={cn("shrink-0 font-mono", x.missing ? "text-text-3" : ROLE_TEXT[x.role ?? ""] ?? "text-text-1")}
                data-hint={x.missing ? undefined : `${x.role ?? "no role yet"} at the decision · band ${fmtPrice(x.lo!)} – ${fmtPrice(x.hi!)}${x.fit ? (x.fit === "inside" ? ", inside the zone" : x.fit === "overlapping" ? ", overlapping the zone" : ", outside the zone") : ""}`}>
                {x.missing ? "—" : fmtPrice(x.price!)}</dd>
            </div>
          );
        })}
        {stack.other > 0 && <div className="flex items-baseline justify-between gap-2"><dt className="text-text-3">Other</dt><dd className="font-mono text-text-3">×{stack.other}</dd></div>}
      </dl>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <><dt className="text-text-3">{k}</dt><dd className="min-w-0 text-text-1">{v}</dd></>;
}

function Readings({ card }: { card: Card }) {
  const groups = useMemo(() => {
    const out = new Map<string, Card["readings"]>();
    for (const r of card.readings) out.set(r.group, [...(out.get(r.group) ?? []), r]);
    return [...out.entries()];
  }, [card]);
  return (
    <section className="rounded-md border border-line bg-bg-1 p-4" aria-label="Detector readings at the decision" data-readings>
      <div className="eyebrow">Detector readings at the decision</div>
      <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(230px,1fr))] gap-x-6 gap-y-4">
        {card.stack && <Stacked stack={card.stack} />}
        {groups.map(([g, rows]) => (
          <div key={g} className="min-w-0">
            <div className="text-[12px] font-medium text-text-2">{g}</div>
            <dl className="mt-1.5 flex flex-col gap-1 text-[12px]">
              {rows.map((r) => (
                <div key={r.label} className="flex items-baseline justify-between gap-2" data-hint={r.note}>
                  <dt className="truncate text-text-3">{r.label}</dt>
                  <dd className="shrink-0 font-mono text-text-1">{r.note ? <span className="text-text-3">{r.note}</span> : r.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
    </section>
  );
}

// ── answering ─────────────────────────────────────────────────────────────────────────────────────────────────────

function AnswerPanel({ round, card, animated, onAnswered }: { round: string; card: Card; animated: boolean; onAnswered: (c: Card, a: Answer) => void }) {
  const [d, setD] = useState<Draft>(() => readDraft(round, card.id));
  const [more, setMore] = useState(() => !!(d.entry_choice || d.stop || d.target || d.confidence));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const reason = useRef<HTMLTextAreaElement>(null);
  const set = (p: Partial<Draft>) => setD((x) => { const n = { ...x, ...p }; writeDraft(round, card.id, n); return n; });
  const ready = !!d.verdict && d.reason.trim().length > 0;
  const submit = async () => {
    if (!ready || busy) { if (!d.verdict) setErr("Pick Take (T) or Skip (S) first."); else if (!d.reason.trim()) { setErr("Write the reason: one or two sentences."); reason.current?.focus(); } return; }
    setBusy(true); setErr(null);
    try {
      const res = await postJson<{ answer: Answer; card: Card }>("/api/review/answer", {
        round, card: card.id, verdict: d.verdict, reason: d.reason, entry_choice: d.entry_choice,
        stop: d.stop || null, target: d.target || null, confidence: d.confidence,
      });
      writeDraft(round, card.id, null);
      onAnswered(res.card, res.answer);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
      if (e instanceof ApiError && e.status === 409) notify({ kind: "warn", title: "Already answered", text: e.message, source: "Review" });
    } finally { setBusy(false); }
  };
  // T / S pick the verdict (not while typing), Ctrl+Enter answers from anywhere on the page
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); void submit(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || document.querySelector("[role=dialog]")) return;
      if (e.key === "t" || e.key === "T") { e.preventDefault(); set({ verdict: "take" }); reason.current?.focus(); }
      else if (e.key === "s" || e.key === "S") { e.preventDefault(); set({ verdict: "skip" }); reason.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  return (
    <motion.section aria-label="Your answer" data-answer
      initial={animated ? { opacity: 0, y: 8 } : false} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, transition: { duration: animated ? 0.12 : 0 } }}
      transition={{ duration: animated ? 0.2 : 0 }}
      className="rounded-md border border-line-strong bg-bg-2 p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="eyebrow">Your answer</div>
        <div className="text-[11px] text-text-3">the outcome shows once you answer</div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2" role="radiogroup" aria-label="Take or skip">
        <VerdictBtn on={d.verdict === "take"} onClick={() => set({ verdict: "take" })} label="Take" k="T" />
        <VerdictBtn on={d.verdict === "skip"} onClick={() => set({ verdict: "skip" })} label="Skip" k="S" />
      </div>
      <label className="eyebrow mt-3 block" htmlFor="cf-reason">Why <span className="normal-case tracking-normal text-text-3">(one or two sentences, required)</span></label>
      <textarea id="cf-reason" ref={reason} rows={3} value={d.reason} onChange={(e) => set({ reason: e.target.value })} maxLength={600}
        placeholder="e.g. Held the ON high twice already; the King sits right above."
        className="mt-1.5 w-full resize-none rounded-md border border-line-strong bg-bg-1 px-3 py-2 text-[13px] leading-5 text-text-1 placeholder:text-text-3 focus:border-cream/60 focus:outline-none" data-reason />
      <button type="button" onClick={() => setMore((m) => !m)} aria-expanded={more}
        className="mt-1.5 inline-flex h-7 items-center gap-1 rounded-full pr-2 text-[12px] text-text-2 hover:text-text-1">
        <ChevronDown className={cn("size-4 transition-transform", more && "rotate-180")} aria-hidden="true" />Optional: entry, stop, target, confidence
      </button>
      <AnimatePresence initial={false}>
        {more && (
          <motion.div initial={animated ? { height: 0, opacity: 0 } : false} animate={{ height: "auto", opacity: 1 }} exit={animated ? { height: 0, opacity: 0 } : { opacity: 0, transition: { duration: 0 } }}
            transition={{ duration: animated ? 0.2 : 0 }} className="overflow-hidden">
            <div className="flex flex-col gap-3 pb-1 pt-2" data-more>
              <fieldset>
                <legend className="eyebrow mb-1.5">How you'd enter</legend>
                <div className="flex flex-wrap gap-1">
                  {ENTRY_CHOICES.map(([id, name]) => (
                    <button key={id} type="button" aria-pressed={d.entry_choice === id} onClick={() => set({ entry_choice: d.entry_choice === id ? null : id })}
                      className={cn("h-7 rounded-full border px-2.5 text-[12px]", d.entry_choice === id ? "border-cream bg-caramel text-cream" : "border-line-strong text-text-2 hover:text-text-1")}>{name}</button>
                  ))}
                </div>
              </fieldset>
              <div className="grid grid-cols-2 gap-3">
                <PriceField label="Your stop" value={d.stop} onChange={(v) => set({ stop: v })} placeholder={fmtPrice(card.trade.stop)} />
                <PriceField label="Your target" value={d.target} onChange={(v) => set({ target: v })} placeholder={fmtPrice(card.trade.target)} />
              </div>
              <fieldset>
                <legend className="eyebrow mb-1.5">Confidence</legend>
                <div className="flex gap-1" role="radiogroup" aria-label="Confidence 1 to 5">
                  {[1, 2, 3, 4, 5].map((n) => (
                    <button key={n} type="button" role="radio" aria-checked={d.confidence === n} onClick={() => set({ confidence: d.confidence === n ? null : n })}
                      className={cn("size-8 rounded-xs border font-mono text-[13px]", d.confidence === n ? "border-cream bg-caramel text-cream" : "border-line-strong bg-bg-1 text-text-2 hover:text-text-1")}>{n}</button>
                  ))}
                </div>
              </fieldset>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <button type="button" onClick={() => void submit()} disabled={busy} data-submit
        className={cn("mt-3 inline-flex h-10 w-full items-center justify-center gap-2 rounded-full text-[13px] font-medium", ready ? "bg-cream text-bg-0 hover:bg-[#ffe9d4]" : "bg-bg-3 text-text-2")}>
        {busy ? "Saving…" : "Answer and reveal"}<Kbd keys={["Ctrl", "Enter"]} className={ready ? "[&_kbd]:border-bg-0/20 [&_kbd]:bg-bg-0/10 [&_kbd]:text-bg-0" : ""} />
      </button>
      {err && <div className="pt-2 text-[12px] text-crit" role="alert">{err}</div>}
    </motion.section>
  );
}

function VerdictBtn({ on, onClick, label, k }: { on: boolean; onClick: () => void; label: string; k: string }) {
  return (
    <button type="button" role="radio" aria-checked={on} onClick={onClick}
      className={cn("inline-flex h-10 items-center justify-center gap-2 rounded-full border px-3.5 text-[14px]", on ? "border-cream bg-caramel text-cream" : "border-line-strong bg-bg-2 text-text-1 hover:border-text-3")}>
      {label}<Kbd keys={[k]} />
    </button>
  );
}

function PriceField({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="eyebrow">{label}</span>
      <input inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value.replace(/[^0-9.]/g, ""))} placeholder={placeholder}
        className="h-8 w-full rounded-md border border-line-strong bg-bg-1 px-2.5 font-mono text-[13px] text-text-1 placeholder:text-text-3 focus:border-cream/60 focus:outline-none" />
    </label>
  );
}

function ResultCard({ card, animated, onNext }: { card: Card; animated: boolean; onNext: () => void }) {
  const o = card.outcome!, a = card.answer!;
  const r = o.r ?? 0;
  const good = r > 0;
  return (
    <motion.section role="status" aria-live="polite" data-result
      initial={animated ? { opacity: 0, y: -14, scale: 0.96 } : false} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, transition: { duration: animated ? 0.12 : 0 } }}
      transition={animated ? { type: "spring", stiffness: 420, damping: 22, mass: 0.9 } : { duration: 0 }}
      className="rounded-md border border-line-strong bg-bg-2 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          {o.result === "target" ? <CircleCheck className="size-6 text-ok" aria-hidden="true" /> : o.result === "stop" ? <CircleX className="size-6 text-crit" aria-hidden="true" /> : <RotateCcw className="size-6 text-text-2" aria-hidden="true" />}
          <div className="font-display text-[26px] leading-7 text-text-1">{RESULT_NAME[o.result] ?? o.result}</div>
        </div>
        <div className={cn("flex items-center gap-1 font-mono text-[20px]", good ? "text-pnl-up" : r < 0 ? "text-pnl-down" : "text-text-2")} data-net-r>
          {good ? <ArrowUp className="size-5" aria-hidden="true" /> : r < 0 ? <ArrowDown className="size-5" aria-hidden="true" /> : null}{rr(r)}
        </div>
      </div>
      <div className="mt-1 font-mono text-[12px] text-text-3">{o.t_fill ? `filled ${clockOf(o.t_fill, card.et_offset_sec)}` : "not filled"}{o.t_exit ? ` · out ${clockOf(o.t_exit, card.et_offset_sec)}` : ""}{o.timeout_reason ? ` · ${o.timeout_reason.replace("_", "-")}` : ""}</div>
      <div className={cn("mt-3 text-[14px] font-medium", a.correct ? "text-ok" : "text-crit")}>{a.correct ? "Agreed with the outcome" : "Against the outcome"}</div>
      <div className="text-[13px] text-text-2">You said {a.verdict}{a.confidence ? ` · confidence ${a.confidence}` : ""}</div>
      <dl className="mt-3 grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1 text-[12px]">
        <dt className="text-text-3">Structure label</dt><dd className="text-text-1">{o.structure ?? "—"}</dd>
        <dt className="text-text-3">Worst / best</dt><dd className="font-mono text-text-1">{o.mae?.toFixed(2) ?? "—"} / {o.mfe?.toFixed(2) ?? "—"} pts</dd>
        {(card.why || card.stratum) && <><dt className="text-text-3">How it was picked</dt><dd className="text-text-1">{card.why ?? card.stratum}</dd></>}
      </dl>
      <p className="mt-2 text-[12px] leading-4 text-text-3">Your reason: {a.reason}</p>
      <button type="button" onClick={onNext} className="mt-3 inline-flex h-10 w-full items-center justify-center gap-2 rounded-full bg-cream text-[13px] font-medium text-bg-0 hover:bg-[#ffe9d4]" data-next autoFocus>
        Next card<Kbd keys={["N"]} className="[&_kbd]:border-bg-0/20 [&_kbd]:bg-bg-0/10 [&_kbd]:text-bg-0" />
      </button>
    </motion.section>
  );
}
