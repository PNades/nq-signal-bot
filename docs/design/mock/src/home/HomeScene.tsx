// Home (design-system/pages/home.md): the whole content area is one CreamFlow scene; the page
// navigation lives in it as nodes linked to the title by thin flowing lines.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Activity, Map, Wallet, SlidersHorizontal, ScanEye, ChartNoAxesCombined, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { PourTitle, FlowTitle, TraceTitle, useFontsReady, type ConceptProps } from "./concepts";
import { cornerNodePos, layoutTitle, solveLines, type NodeSlot } from "./titleLayout";

type Concept = "pour" | "flow" | "trace";
const CONCEPTS: Record<Concept, (p: ConceptProps) => React.ReactNode> = { pour: PourTitle, flow: FlowTitle, trace: TraceTitle };

interface NodeDef { key: string; id: string; name: string; icon: LucideIcon; x: number; y: number; slot: NodeSlot; label: string; value: React.ReactNode; note: string; disabled?: boolean }

function nextWindow(): { value: string; note: string } {
  // recomputed from the clock on every render tick, never counted down (MASTER §1.2)
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }).formatToParts(new Date());
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const mins = Number(get("hour")) * 60 + Number(get("minute"));
  const wd = get("weekday");
  const weekend = wd === "Sat" || wd === "Sun";
  const open = 9 * 60 + 45;
  if (!weekend && mins < open) { const d = open - mins; return { value: `in ${Math.floor(d / 60)}h ${String(d % 60).padStart(2, "0")}m`, note: "setup window 09:45 ET" }; }
  return { value: "Mon 09:30", note: "next RTH open" };
}

// Opening sequence (pages/home.md): the full intro plays on the first open of Home in a session; later visits show the
// formed scene (the page wrapper fades it in). `&intro=1` replays it on every open while we design.
const INTRO_SEEN_KEY = "creamflow.homeIntroSeen";
let introSeenHere = false;
function introSeen() {
  try { return introSeenHere || sessionStorage.getItem(INTRO_SEEN_KEY) === "1"; } catch { return introSeenHere; }
}
function markIntroSeen() {
  introSeenHere = true;
  try { sessionStorage.setItem(INTRO_SEEN_KEY, "1"); } catch { /* storage blocked: this page load still remembers */ }
}

// nodes arrive clockwise from the top, one every NODE_STAGGER_S; each line draws out from its node to the word as the
// node arrives (about 1.7 s for all six)
const ARRIVAL_ORDER = ["live", "review", "performance", "settings", "accounts", "gameplan"];
const NODE_STAGGER_S = 0.2, NODE_IN_S = 0.35, LINE_DELAY_S = 0.08, LINE_DRAW_S = 0.6;

export function HomeScene({ concept, calm, forceIntro, onOpenPage }: {
  concept: Concept; calm: boolean; forceIntro: boolean;
  /** opens a page that exists; returns false when it doesn't yet (the node then shows "Opening …") */
  onOpenPage?: (id: string) => boolean;
}) {
  const [play] = useState(() => forceIntro || !introSeen());
  useEffect(() => { markIntroSeen(); }, []);
  const [phase, setPhase] = useState<"title" | "nodes" | "idle">(play ? "title" : "idle");
  const [skip, setSkip] = useState(!play);
  // how many nodes have arrived (ARRIVAL_ORDER); all six at once in Calm, on a skip and on a later visit
  const [arrived, setArrived] = useState(play ? 0 : 6);
  const [hover, setHover] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [, setTick] = useState(0);
  const sceneRef = useRef<HTMLDivElement>(null);
  // the content area's size, followed continuously (window resize, sidebar collapse): the whole scene
  // (title size and position, particles, nodes, lines) is laid out again from it (owner 2026-11-14)
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const fonts = useFontsReady();

  useEffect(() => { const id = setInterval(() => setTick((v) => v + 1), 15000); return () => clearInterval(id); }, []);
  useEffect(() => {
    const el = sceneRef.current; if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const w = Math.round(e.contentRect.width), h = Math.round(e.contentRect.height);
      setSize((s) => (s && s.w === w && s.h === h ? s : { w, h }));
    });
    ro.observe(el); return () => ro.disconnect();
  }, []);

  const onSettled = useCallback(() => setPhase((p) => (p === "title" ? "nodes" : p)), []);
  useEffect(() => {
    if (phase !== "nodes") return;
    if (calm || skip) { setArrived(6); setPhase("idle"); return; }
    const timers = Array.from({ length: 6 }, (_, k) => setTimeout(() => setArrived((a) => Math.max(a, k + 1)), k * NODE_STAGGER_S * 1000));
    timers.push(setTimeout(() => setPhase("idle"), (5 * NODE_STAGGER_S + LINE_DELAY_S + LINE_DRAW_S) * 1000));
    return () => timers.forEach(clearTimeout);
  }, [phase, calm, skip]);
  useEffect(() => { if (skip) setArrived(6); }, [skip]);

  const win = nextWindow();
  const nodes: NodeDef[] = useMemo(() => [
    { key: "1", id: "live", name: "Live", icon: Activity, x: 0.5, y: 0.08, slot: "top", label: "Engine", value: "—", note: "Live arrives at step 21", disabled: true },
    { key: "2", id: "gameplan", name: "Gameplan", icon: Map, x: 0.13, y: 0.19, slot: "top-left", label: "Next window", value: win.value, note: win.note },
    { key: "3", id: "accounts", name: "Accounts", icon: Wallet, x: 0.13, y: 0.74, slot: "bottom-left", label: "Closest drawdown margin", value: "$1,240", note: "mffur1 · 2.5 lives" },
    { key: "4", id: "settings", name: "Settings", icon: SlidersHorizontal, x: 0.5, y: 0.9, slot: "bottom", label: "Risk per trade", value: "$500", note: "1/3 of drawdown · mffur1" },
    { key: "5", id: "review", name: "Review", icon: ScanEye, x: 0.87, y: 0.19, slot: "top-right", label: "Cards left", value: "32 / 50", note: "round 1 · development months" },
    { key: "6", id: "performance", name: "Performance", icon: ChartNoAxesCombined, x: 0.87, y: 0.74, slot: "bottom-right",
      label: "November P&L", value: <span className="text-pnl-up"><span className="mr-1 text-[0.7em]">▲</span>+$2,601.25</span>, note: "backtest · months 1–6" },
  ], [win.value, win.note]);

  const open = useCallback((n: NodeDef) => {
    if (!n.disabled && onOpenPage?.(n.id)) return;
    setToast(n.disabled ? n.note : `Opening ${n.name}…`);
    setTimeout(() => setToast(null), 1600);
  }, [onOpenPage]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (phase !== "idle") setSkip(true);
      const n = nodes.find((d) => d.key === e.key);
      // a key opens a page: any open hover card closes, so a message never appears under one
      if (n) { setHover(null); open(n); }
    };
    const onClick = () => { if (phase !== "idle") setSkip(true); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onClick);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onClick); };
  }, [phase, nodes, open]);

  const Title = CONCEPTS[concept];
  const layout = useMemo(() => (fonts && size ? layoutTitle(size.w, size.h) : null), [fonts, size]);
  const showNodes = phase !== "title" && layout !== null;
  // mock only: lets the screenshot script measure the line gaps and the layout after a resize
  useEffect(() => { (window as unknown as { __cfLayout?: unknown }).__cfLayout = layout; }, [layout]);
  // the corner nodes are placed relative to the wordmark (cornerNodePos: mirrored left-right and top-bottom, at their
  // 960-px place at every width, owner 2026-11-15); Live and Settings stay at fixed shares of the scene
  const posOf = (n: NodeDef): [number, number] => {
    if (layout && n.slot.includes("-")) { const [x, y] = cornerNodePos(n.slot, layout); return [x / layout.W, y / layout.H]; }
    return [n.x, n.y];
  };
  // only the layout changes the node positions and the lines
  const lineGeo = useMemo(
    () => (layout ? solveLines(layout, nodes.map((n) => ({ id: n.id, slot: n.slot, x: posOf(n)[0] * layout.W, y: posOf(n)[1] * layout.H }))) : null),
    [layout], // eslint-disable-line react-hooks/exhaustive-deps
  );
  useEffect(() => {
    (window as unknown as { __cfLines?: unknown }).__cfLines = lineGeo ? nodes.map((n) => ({ id: n.id, ...lineGeo[n.id] })) : null;
  }, [lineGeo, nodes]);

  return (
    <div ref={sceneRef} className="absolute inset-0 overflow-hidden bg-bg-0">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0" style={{ background: "radial-gradient(60% 50% at 50% 47%, color-mix(in oklab, var(--color-gold) 6%, transparent), transparent 70%)" }} />
      {concept === "pour" ? (
        <div className="absolute left-1/2 w-[min(82%,1100px)] -translate-x-1/2 -translate-y-1/2" style={{ top: "44%" }}>
          <Title skip={skip} still={calm} onSettled={onSettled} />
        </div>
      ) : (
        <Title skip={skip} still={calm} onSettled={onSettled} layout={layout} />
      )}

      <svg className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
        {showNodes && nodes.map((n) => {
          // gently curved, aimed into the word, ending a fixed gap before the letter in front (titleLayout.ts); the
          // path runs from the node to the word, so drawing it out sends it towards the word
          const d = lineGeo![n.id].d;
          const lit = hover === n.id;
          const here = arrived > ARRIVAL_ORDER.indexOf(n.id);
          const instant = calm || skip;
          return (
            <g key={n.id}>
              <motion.path d={d} fill="none" className={lit ? "stroke-gold" : "stroke-gold/30"} strokeWidth={lit ? 1.3 : 1}
                initial={{ pathLength: instant && here ? 1 : 0, opacity: instant && here ? (n.disabled ? 0.35 : 1) : 0 }}
                animate={{ pathLength: here ? 1 : 0, opacity: here ? (n.disabled ? 0.35 : 1) : 0 }}
                transition={{ duration: instant ? 0 : LINE_DRAW_S, delay: instant ? 0 : LINE_DELAY_S, ease: [0.3, 0, 0, 1] }} />
              {!calm && !n.disabled && (
                <path d={d} fill="none" className="stroke-cream/55" strokeWidth={1.4} strokeDasharray="2 22" strokeLinecap="round"
                  style={{ animation: `flowdash ${lit ? 1.2 : 2.6}s linear infinite`, opacity: phase === "idle" ? 1 : 0, transition: "opacity .6s" }} />
              )}
            </g>
          );
        })}
        <style>{`@keyframes flowdash { to { stroke-dashoffset: -24 } }`}</style>
      </svg>

      {showNodes && nodes.map((n) => {
        const Icon = n.icon;
        const lit = hover === n.id;
        const here = arrived > ARRIVAL_ORDER.indexOf(n.id);
        const instant = calm || skip;
        return (
          <motion.button
            key={n.id}
            onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(n.id)} onBlur={() => setHover(null)}
            onClick={() => open(n)}
            aria-label={`${n.name} (key ${n.key})${n.disabled ? ", not available yet" : ""}`}
            initial={instant && here ? { opacity: n.disabled ? 0.55 : 1, scale: 1 } : { opacity: 0, scale: 0.85 }}
            animate={here ? { opacity: n.disabled ? 0.55 : 1, scale: 1 } : { opacity: 0, scale: 0.85 }}
            transition={{ duration: instant ? 0 : NODE_IN_S, ease: [0, 0, 0.2, 1] }}
            tabIndex={here ? 0 : -1}
            className="absolute z-10 -translate-x-1/2 -translate-y-1/2 text-left"
            style={{ left: `${posOf(n)[0] * 100}%`, top: `${posOf(n)[1] * 100}%`, pointerEvents: here ? "auto" : "none" }}
          >
            <motion.div layout transition={{ type: "spring", stiffness: 420, damping: 34 }}
              className={cn(
                "overflow-hidden rounded-lg border bg-bg-2/60 backdrop-blur-md",
                lit ? "border-gold/50 shadow-[0_0_28px_color-mix(in_oklab,var(--color-gold)_18%,transparent)]" : "border-white/[.08]",
              )}>
              <motion.div layout="position" className="flex h-11 items-center gap-2.5 px-3.5">
                <span className={cn("grid size-7 place-items-center rounded-full border", lit ? "border-gold/60 bg-gold/15 text-gold" : "border-white/10 bg-white/[.04] text-cream")}>
                  <Icon className="size-4" strokeWidth={1.5} aria-hidden="true" />
                </span>
                <span className="text-[14px] font-medium text-text-1">{n.name}</span>
                <kbd className="ml-1 rounded-xs border border-line-strong px-1.5 font-mono text-[10px] text-text-3">{n.key}</kbd>
              </motion.div>
              <AnimatePresence initial={false}>
                {lit && (
                  <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: calm ? 0 : 0.22, ease: [0.2, 0, 0, 1] }} className="w-[230px] px-3.5 pb-3.5">
                    <div className="eyebrow pt-0.5">{n.label}</div>
                    <div className="mt-1.5 font-mono text-[22px] leading-7 text-text-1">{n.value}</div>
                    <div className="mt-[7px] text-[12px] leading-4 text-text-3">{n.note}</div>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          </motion.button>
        );
      })}

      {/* messages live in the bottom-right corner, where no node sits (owner 2026-11-15): the toast above the key hint */}
      {/* one row along the bottom edge (toast, then the key hint), below the bottom-right node at every size measured
          (960 × 1000 to 1920 × 1080, and 1024 × 768 / 1440 × 760) */}
      <div className="pointer-events-none absolute right-6 z-20 flex flex-row items-center gap-3" style={{ bottom: "var(--toast-bottom, 14px)" }} data-home-messages>
        <AnimatePresence>
          {toast && (
            <motion.div role="status" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: calm ? 0 : 0.2 }}
              className="rounded-md border border-line-strong bg-bg-3 px-4 py-2 text-[13px] text-text-1 shadow-[0_12px_32px_rgba(0,0,0,.55)]">
              {toast}
            </motion.div>
          )}
        </AnimatePresence>
        <div className="font-mono text-[11px] text-text-3">
          {phase !== "idle" ? "click or press any key to skip" : "1–6 open a page · Ctrl K search"}
        </div>
      </div>
    </div>
  );
}
