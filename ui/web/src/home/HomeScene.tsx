// Home (SPEC §9b item 0, design-system/pages/home.md; approved 2026-11-15): the whole content area is one CreamFlow
// scene; the page navigation lives in it as six nodes linked to the title by "arcs to the centre". Ported from the
// step-14a mock; changes for the real panel: the node cards show live numbers from /api/home ("—" where nothing exists
// yet: balances come with the engine at step 22, review cards with stage 4, P&L with the step-15 ledgers), every node
// but Live opens its page, keys 1-6 never fire while typing or while a dialog is open, the flowing-dash keyframes live
// in styles.css (the CSP allows no <style> element), and the bottom-left corner stays clear for the date block.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, animate, cubicBezier, motion, useMotionValue } from "motion/react";
import { Activity, ChartNoAxesCombined, Map, ScanEye, SlidersHorizontal, Wallet, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { getJson } from "@/lib/api";
import { peek } from "@/lib/prefetch";
import { PAGE_URL } from "@/lib/nav";
import { isTyping, useNow } from "@/lib/hooks";
import { money0 } from "@/lib/format";
import type { PageId } from "@/shell/pages";
import { FlowTitle, useFontsReady } from "./flowTitle";
import { cornerNodePos, layoutTitle, shiftLayout, solveLines, solveLinesSteps, type LineGeometry, type NodeAt, type NodeSlot, type TitleLayout } from "./titleLayout";
import { bannerHeights, bannerTarget, onBannerTarget } from "@/shell/Banners";
import { GLIDE_S, glideEase } from "@/lib/glide";

interface HomeData {
  now_et: string;
  windows: { rth_open: string; golden_zone_from: string; all_setups_from: string; last_entry: string; flat_by: string };
  accounts: number; drafts: number; risk_per_trade_usd: number[];
  closest_drawdown_margin_usd: number | null; review_cards_left: number | null; month_pnl_usd: number | null;
}

interface NodeDef { key: string; id: PageId; name: string; icon: LucideIcon; x: number; y: number; slot: NodeSlot; label: string; value: React.ReactNode; note: string; disabled?: boolean }

const ET_PARTS = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const toMin = (hhmm: string) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));

/** The next setup window, recomputed from the clock on every tick (never counted down; MASTER §1.2). Weekends and
 *  after the last entry point at the next weekday's golden-zone window (holidays: the Gameplan page has the calendar). */
function nextWindow(now: Date, w: HomeData["windows"] | undefined): { value: string; note: string } {
  if (!w) return { value: "—", note: "loading the session rules" };
  const parts = ET_PARTS.formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const mins = (Number(get("hour")) % 24) * 60 + Number(get("minute"));
  const wd = get("weekday");
  const weekend = wd === "Sat" || wd === "Sun";
  const gz = toMin(w.golden_zone_from), all = toMin(w.all_setups_from), last = toMin(w.last_entry);
  const fmt = (d: number) => `in ${Math.floor(d / 60)}h ${String(d % 60).padStart(2, "0")}m`;
  if (!weekend && mins < gz) return { value: fmt(gz - mins), note: `golden zone opens ${w.golden_zone_from} ET` };
  if (!weekend && mins < all) return { value: fmt(all - mins), note: `all setups from ${w.all_setups_from} ET` };
  if (!weekend && mins < last) return { value: "open now", note: `last entry ${w.last_entry} ET` };
  const next = wd === "Fri" || wd === "Sat" ? "Mon" : wd === "Sun" ? "Mon" : "tomorrow";
  return { value: `${next} ${w.golden_zone_from}`, note: "next golden-zone window (ET)" };
}

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
// node arrives (about 1.7 s for all six; owner 2026-11-15)
const ARRIVAL_ORDER: PageId[] = ["live", "review", "performance", "settings", "accounts", "gameplan"];
const NODE_STAGGER_S = 0.2, NODE_IN_S = 0.35, LINE_DELAY_S = 0.08, LINE_DRAW_S = 0.6;
// a return to Home after the first-open intro (owner 2026-11-16): a short entrance, about 450 ms in all: the wordmark
// fades in with a slight scale-up, the nodes fade in 40 ms apart (same order), each line draws out to the word as its
// node arrives; a click or a key finishes it at once; Calm: none
const RET_WORD_S = 0.3, RET_WORD_FROM = 0.98, RET_STAGGER_S = 0.04, RET_NODE_S = 0.2, RET_LINE_S = 0.22, RET_STEP_MS = 34;
// an open node card (kept 8 px inside the scene) and the node header's height
const CARD_W = 270, CARD_MARGIN = 8, HEADER_H = 44;
const CARD_IN_S = 0.18, CARD_OUT_S = 0.12, CARD_SCALE_FROM = 0.94;
// banner states worked out ahead of time: up to MAX_PRE_BANNERS stacked, each one or two lines tall (both heights
// measured from hidden probe banners); idle slices, forced a few steps if no idle time comes
const MAX_PRE_BANNERS = 3, IDLE_TIMEOUT_MS = 1000, IDLE_FORCED_STEPS = 4;
// a node label's hover: every colour, the glow and the strength on one 180-ms ease-out transition (its line too)
const LABEL_T = "transition-[border-color,background-color,box-shadow,color,opacity] duration-[180ms] ease-out";
const LINE_T = "transition-[stroke,stroke-width] duration-[180ms] ease-out";


export function HomeScene({ calm, onOpenPage, blocked }: {
  calm: boolean; onOpenPage: (id: PageId) => void;
  /** true while a dialog or the palette is open: keys 1-6 do nothing */
  blocked: boolean;
}) {
  const [play] = useState(() => !introSeen());
  // a return (the intro was seen this session): the short return entrance, not in Calm
  const [ret] = useState(() => !play && !calm);
  useEffect(() => { markIntroSeen(); }, []);
  const [phase, setPhase] = useState<"title" | "nodes" | "idle">(play ? "title" : ret ? "nodes" : "idle");
  const [skip, setSkip] = useState(!play && !ret);
  const [arrived, setArrived] = useState(play || ret ? 0 : 6);
  // the return entrance, driven frame by frame from the first frame shown (each frame counts at most RET_STEP_MS, so a
  // late frame slows it instead of bunching the nodes): the wordmark's opacity and scale, then the nodes 40 ms apart
  const wordO = useMotionValue(ret ? 0 : 1), wordS = useMotionValue(ret ? RET_WORD_FROM : 1);
  const skipRef = useRef(skip);
  skipRef.current = skip;
  useEffect(() => {
    if (!ret) return;
    const ease = cubicBezier(0, 0, 0.2, 1);
    const total = (5 * RET_STAGGER_S + Math.max(RET_NODE_S, RET_LINE_S)) * 1000;
    let raf = 0, prev = -1, acc = 0;
    const tick = (ts: number) => {
      if (skipRef.current) return;
      if (prev >= 0) acc += Math.min(Math.max(0, ts - prev), RET_STEP_MS);
      prev = ts;
      const e = ease(Math.min(1, acc / (RET_WORD_S * 1000)));
      wordO.set(e); wordS.set(RET_WORD_FROM + (1 - RET_WORD_FROM) * e);
      const k = Math.min(6, Math.floor(acc / (RET_STAGGER_S * 1000)) + 1);
      setArrived((a) => Math.max(a, k));
      if (acc >= total) { setPhase("idle"); return; }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // a click or a key: everything at its end at once
  useEffect(() => { if (skip) { wordO.set(1); wordS.set(1); } }, [skip]); // eslint-disable-line react-hooks/exhaustive-deps
  const finish = () => { setSkip(true); setArrived(6); setPhase((p) => (p === "nodes" ? "idle" : p)); };
  const [hover, setHover] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  // opens filled: its data was loaded before the page change (lib/nav.ts)
  const [data, setData] = useState<HomeData | null>(() => peek<HomeData>(PAGE_URL.home) ?? null);
  const now = useNow(15000);
  const sceneRef = useRef<HTMLDivElement>(null);
  // the scene's geometry: width, the banner-free height c (the scene plus the banners above it) and the scene's own
  // height h = c - the banners' target height; the scene is anchored to the bottom, so its box jumps to the final place
  // at once while the banners above unfold or fold
  const [geo, setGeo] = useState<{ w: number; c: number; cRaw: number; h: number } | null>(null);
  const geoRef = useRef(geo);
  geoRef.current = geo;
  const fonts = useFontsReady();

  useEffect(() => {
    let live = true;
    const load = () => getJson<HomeData>(PAGE_URL.home).then((d) => live && setData(d)).catch(() => { /* numbers stay "—" */ });
    load();
    const id = setInterval(load, 30000);
    return () => { live = false; clearInterval(id); };
  }, []);
  // The banner glide (owner 2026-11-15). Only a banner arriving or leaving glides (onBannerTarget); any other real size
  // change (1 px or more) re-lays out at once, and smaller ones are ignored. The wordmark keeps the size of the
  // banner-free layout, so each banner state's layout is that layout moved down (shiftLayout, no raster): ready in the
  // same commit as the banner, and each part starts exactly where it was seen and glides home once, on the banner's own
  // timing. The wordmark (canvas, its own centre and size) and each node (its own point; the
  // nodes don't follow the wordmark's scale, e.g. at 960 px the wordmark keeps its size while the top and bottom nodes
  // move apart) glide separately, on the same 250-ms curve. Calm: held, then placed at once.
  const fx = useMotionValue(0), fy = useMotionValue(0), fs = useMotionValue(1);
  const glide = useRef<ReturnType<typeof animate>[]>([]);
  const stageRef = useRef<HTMLDivElement>(null);
  const flipFrom = useRef<{ x: number; y: number; size: number } | null>(null);
  // the placed layout's centre and size in the scene, and the scene's box on screen, as last seen
  const placed = useRef<{ cx: number; cy: number; size: number } | null>(null);
  // each node's point in the placed layout (scene px), its current glide offset, and where it was seen at a banner event
  const placedPts = useRef<Record<string, [number, number]>>({});
  const nodeOff = useRef<Record<string, [number, number]>>({});
  const nodeFrom = useRef<Record<string, [number, number]> | null>(null);
  // a node's element is moved by a transform, written directly (several per frame, no React render)
  const setNodeOffset = (id: string, x: number, y: number) => {
    nodeOff.current[id] = [x, y];
    sceneRef.current?.querySelectorAll<HTMLElement | SVGGElement>(`[data-glide="${id}"]`).forEach((e) => { e.style.transform = x || y ? `translate(${x}px, ${y}px)` : ""; });
  };
  // each line's cubic as shown on screen (8 numbers: start, two controls, end; screen px), and as it was seen at a banner
  // event: during a glide every point moves from the old shape to the new one on the nodes' curve, so the node end stays
  // on its node and no line changes shape in one frame (owner 2026-11-15)
  const lineShown = useRef<Record<string, number[]>>({});
  const lineFrom = useRef<Record<string, number[]> | null>(null);
  const setLine = (id: string, pts: number[], box: { left: number; top: number }) => {
    lineShown.current[id] = pts;
    const q = pts.map((v, i) => (v - (i % 2 ? box.top : box.left)).toFixed(2));
    const d = `M${q[0]},${q[1]} C${q[2]},${q[3]} ${q[4]},${q[5]} ${q[6]},${q[7]}`;
    sceneRef.current?.querySelectorAll<SVGPathElement>(`[data-line="${id}"] path`).forEach((e) => e.setAttribute("d", d));
  };
  const sceneBox = useRef<{ left: number; top: number } | null>(null);
  // where everything is seen right now (a glide may still be running): the start of the next glide
  const captureSeen = () => {
    const was = sceneBox.current, p = placed.current;
    if (!was || !p) { flipFrom.current = null; nodeFrom.current = null; lineFrom.current = null; return; }
    glide.current.forEach((c) => c.stop());
    flipFrom.current = { x: was.left + p.cx + fx.get(), y: was.top + p.cy + fy.get(), size: p.size * fs.get() };
    const seen: Record<string, [number, number]> = {};
    for (const [id, [px, py]] of Object.entries(placedPts.current)) {
      const [ox, oy] = nodeOff.current[id] ?? [0, 0];
      seen[id] = [was.left + px + ox, was.top + py + oy];
    }
    nodeFrom.current = seen;
    lineFrom.current = Object.fromEntries(Object.entries(lineShown.current).map(([id, pts]) => [id, [...pts]]));
  };
  // a banner arrives or leaves: reported in the banners' own commit, so the new scene and the start of the glide are in
  // place before the first frame of the banner's animation is painted (0 frames between the two)
  useEffect(() => onBannerTarget((t) => {
    const g = geoRef.current;
    if (!g) return;
    const h = Math.round(g.cRaw - t);
    if (h === g.h) return;
    captureSeen();
    setGeo({ ...g, h });
  }), []);
  // the window or anything else changing the banner-free height: laid out again at once (no glide). The parent shrinks
  // exactly as much as the banners grow while they unfold or fold, so c stays the same through a banner's animation;
  // changes under 1 px are ignored. Measured before the first paint (owner 2026-11-16: Home complete in its first frame).
  useLayoutEffect(() => {
    const el = sceneRef.current, parent = el?.parentElement; if (!parent) return;
    const measure = () => {
      const pr = parent.getBoundingClientRect();
      const bh = document.querySelector("[data-banners]")?.getBoundingClientRect().height ?? 0;
      const cRaw = pr.height + bh, w = Math.round(pr.width);
      const g = geoRef.current;
      if (g && Math.abs(g.cRaw - cRaw) < 1 && g.w === w) return;
      flipFrom.current = null; nodeFrom.current = null;
      setGeo({ w, c: Math.round(cRaw), cRaw, h: Math.round(cRaw - bannerTarget()) });
    };
    const ro = new ResizeObserver(measure);
    ro.observe(parent); measure();
    return () => ro.disconnect();
  }, []);

  const onSettled = useCallback(() => setPhase((p) => (p === "title" ? "nodes" : p)), []);
  useEffect(() => {
    if (phase !== "nodes" || ret) return;
    if (calm || skip) { setArrived(6); setPhase("idle"); return; }
    const stagger = ret ? RET_STAGGER_S : NODE_STAGGER_S;
    const timers = Array.from({ length: 6 }, (_, k) => setTimeout(() => setArrived((a) => Math.max(a, k + 1)), k * stagger * 1000));
    timers.push(setTimeout(() => setPhase("idle"), (ret ? 5 * RET_STAGGER_S + Math.max(RET_NODE_S, RET_LINE_S) : 5 * NODE_STAGGER_S + LINE_DELAY_S + LINE_DRAW_S) * 1000));
    return () => timers.forEach(clearTimeout);
  }, [phase, calm, skip]);
  useEffect(() => { if (skip) setArrived(6); }, [skip]);

  const win = nextWindow(now, data?.windows);
  const risks = data?.risk_per_trade_usd ?? [];
  const nodes: NodeDef[] = useMemo(() => [
    { key: "1", id: "live", name: "Live", icon: Activity, x: 0.5, y: 0.08, slot: "top", label: "Engine", value: "—", note: "Live arrives at step 21", disabled: true },
    { key: "2", id: "gameplan", name: "Gameplan", icon: Map, x: 0.13, y: 0.19, slot: "top-left", label: "Next setup window", value: win.value, note: win.note },
    { key: "3", id: "accounts", name: "Accounts", icon: Wallet, x: 0.13, y: 0.74, slot: "bottom-left", label: "Closest drawdown margin",
      value: data?.closest_drawdown_margin_usd != null ? money0(data.closest_drawdown_margin_usd) : "—",
      note: data ? `balances arrive with the engine (step 22) · ${data.accounts} account${data.accounts === 1 ? "" : "s"}${data.drafts ? `, ${data.drafts} draft` : ""}` : "loading" },
    { key: "4", id: "settings", name: "Settings", icon: SlidersHorizontal, x: 0.5, y: 0.9, slot: "bottom", label: "Risk per trade",
      value: risks.length ? risks.map((r) => money0(r)).join(" · ") : "—", note: risks.length ? "per account, all-in (§6.1)" : "no accounts yet" },
    { key: "5", id: "review", name: "Review", icon: ScanEye, x: 0.87, y: 0.19, slot: "top-right", label: "Cards left",
      value: data?.review_cards_left != null ? String(data.review_cards_left) : "—", note: "the review loop comes in build stage 4" },
    { key: "6", id: "performance", name: "Performance", icon: ChartNoAxesCombined, x: 0.87, y: 0.74, slot: "bottom-right", label: "This month's P&L",
      value: data?.month_pnl_usd != null ? money0(data.month_pnl_usd) : "—", note: "no trade ledger yet (from step 15)" },
  ], [win.value, win.note, data, risks]);

  const open = useCallback((n: NodeDef) => {
    if (n.disabled) { setToast(n.note); setTimeout(() => setToast(null), 1600); return; }
    onOpenPage(n.id);
  }, [onOpenPage]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (blocked || isTyping(e) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (phase !== "idle") { if (ret) finish(); else setSkip(true); }
      const n = nodes.find((d) => d.key === e.key);
      if (n) { setHover(null); open(n); }
    };
    const onClick = () => { if (phase !== "idle") { if (ret) finish(); else setSkip(true); } };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onClick);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onClick); };
  }, [phase, nodes, open, blocked]);

  // the banner-free layout (rastered once per window size) and the scene's layout = that one moved for the banners
  const base = useMemo(() => (fonts && geo ? layoutTitle(geo.w, geo.c) : null), [fonts, geo?.w, geo?.c]); // eslint-disable-line react-hooks/exhaustive-deps
  const layout = useMemo(() => (base && geo ? shiftLayout(base, geo.h) : null), [base, geo?.h]); // eslint-disable-line react-hooks/exhaustive-deps
  const [origin, setOrigin] = useState("50% 50%");
  useLayoutEffect(() => {
    const el = sceneRef.current;
    if (!layout || !el) return;
    const r = el.getBoundingClientRect();
    sceneBox.current = { left: r.left, top: r.top };
    placed.current = { cx: layout.cx, cy: layout.cy, size: layout.size };
    const pts: Record<string, [number, number]> = {};
    for (const n of nodes) { const [x, y] = posOf(n); pts[n.id] = [x * layout.W, y * layout.H]; }
    placedPts.current = pts;
    const from = flipFrom.current, seen = nodeFrom.current, linesSeen = lineFrom.current;
    flipFrom.current = null; nodeFrom.current = null; lineFrom.current = null;
    glide.current.forEach((c) => c.stop());
    // the new lines in screen px
    const box = { left: r.left, top: r.top };
    const lineEnd: Record<string, number[]> = {};
    for (const [id, g] of Object.entries(lineGeo ?? {})) {
      const v = (g.d.match(/-?\d+(?:\.\d+)?/g) ?? []).map(Number);
      if (v.length === 8) lineEnd[id] = v.map((x, i) => x + (i % 2 ? box.top : box.left));
    }
    if (!from || !seen || calm) {
      fx.set(0); fy.set(0); fs.set(1);
      if (stageRef.current) stageRef.current.style.transform = "";
      for (const id of Object.keys(pts)) setNodeOffset(id, 0, 0);
      for (const [id, e] of Object.entries(lineEnd)) setLine(id, e, box);
      return;
    }
    // each part starts exactly where it was seen and glides home once (ease-out with a gentle start, so no frame jumps far)
    setOrigin(`${layout.cx}px ${layout.cy}px`);
    fx.set(from.x - (r.left + layout.cx)); fy.set(from.y - (r.top + layout.cy)); fs.set(from.size / layout.size);
    if (stageRef.current) stageRef.current.style.transform = `translateX(${fx.get()}px) translateY(${fy.get()}px) scale(${fs.get()})`;
    const start: Record<string, [number, number]> = {};
    for (const [id, [px, py]] of Object.entries(pts)) {
      const s0 = seen[id];
      start[id] = s0 ? [s0[0] - (r.left + px), s0[1] - (r.top + py)] : [0, 0];
      setNodeOffset(id, start[id][0], start[id][1]);
    }
    // each line starts as it was shown and morphs point by point to its new shape
    const lineStart: Record<string, number[]> = {};
    for (const [id, e] of Object.entries(lineEnd)) { lineStart[id] = linesSeen?.[id] ?? e; setLine(id, lineStart[id], box); }
    const t = { duration: GLIDE_S, ease: glideEase };
    glide.current = [animate(fx, 0, t), animate(fy, 0, t), animate(fs, 1, t),
      animate(1, 0, { ...t, onUpdate: (v) => {
        for (const [id, [sx, sy]] of Object.entries(start)) setNodeOffset(id, sx * v, sy * v);
        for (const [id, e] of Object.entries(lineEnd)) { const s0 = lineStart[id]; setLine(id, e.map((x, i) => x + (s0[i] - x) * v), box); }
      } })];
  }, [layout, calm, fx, fy, fs]);
  const showNodes = phase !== "title" && layout !== null;
  const posOf = (n: NodeDef): [number, number] => {
    if (layout && n.slot.includes("-")) { const [x, y] = cornerNodePos(n.slot, layout); return [x / layout.W, y / layout.H]; }
    return [n.x, n.y];
  };
  // a node's point in px (fixed for a given layout), and the shift of its open card so it stays CARD_MARGIN inside the scene
  const pointOf = (n: NodeDef): [number, number] => {
    const [x, y] = posOf(n);
    return layout ? [x * layout.W, y * layout.H] : [0, 0];
  };
  const cardShift = (x: number) => (layout ? Math.max(CARD_W / 2 + CARD_MARGIN, Math.min(layout.W - CARD_W / 2 - CARD_MARGIN, x)) - x : 0);
  // the lines of each banner state, solved once per height for the current banner-free layout
  const lineCache = useRef<{ base: TitleLayout | null; byH: globalThis.Map<number, Record<string, LineGeometry>> }>({ base: null, byH: new globalThis.Map() });
  const linesFor = (b: TitleLayout) => {
    if (lineCache.current.base !== b) lineCache.current = { base: b, byH: new globalThis.Map() };
    return lineCache.current.byH;
  };
  const nodesAt = (L: TitleLayout): NodeAt[] => nodes.map((n) => {
    const [x, y] = n.slot.includes("-") ? cornerNodePos(n.slot, L) : [n.x * L.W, n.y * L.H];
    return { id: n.id, slot: n.slot, x, y };
  });
  const lineGeo = useMemo(() => {
    if (!layout || !base) return null;
    const cache = linesFor(base);
    let g = cache.get(layout.H);
    if (!g) {
      g = solveLines(layout, nodesAt(layout)); cache.set(layout.H, g);
      // a banner state that wasn't worked out ahead of time (checked by the build-stage screenshots)
      if (layout.H !== base.H) sceneRef.current?.setAttribute("data-lines-solved-late", String(layout.H));
    }
    return g;
  }, [layout, base]); // eslint-disable-line react-hooks/exhaustive-deps
  // Ahead of time, while the page is idle after loading (owner 2026-11-15): the lines of every banner state (0 to 3
  // stacked banners, each one or two lines tall) are solved in small idle slices (one candidate aim at a time), so a
  // banner arriving or leaving finds its layout and lines ready: the layout is the banner-free one moved (shiftLayout)
  // and the lines come from the cache. A taller banner (3+ lines) is solved when it arrives.
  useEffect(() => {
    if (!base || !geo) return;
    const [one, two] = bannerHeights();
    if (!one) return;
    const cache = linesFor(base);
    const todo: number[] = [];
    // k banners stacked, j of them two lines tall (fewest banners first: the likeliest states are ready soonest)
    for (let k = 0; k <= MAX_PRE_BANNERS; k++) for (let j = 0; j <= k; j++) {
      const h = Math.round(geo.cRaw - (k - j) * one - j * two);
      if (!cache.has(h) && !todo.includes(h)) todo.push(h);
    }
    let gen: Generator<void, Record<string, LineGeometry>, void> | null = null, h = 0, cancelled = false, id = 0;
    const work = (dl: IdleDeadline) => {
      let forced = dl.didTimeout ? IDLE_FORCED_STEPS : 0;
      while (!cancelled && (dl.timeRemaining() > 1 || forced-- > 0)) {
        if (!gen) {
          const next = todo.shift();
          if (next === undefined) return;
          if (cache.has(next)) continue;
          h = next;
          const L = shiftLayout(base, h);
          gen = solveLinesSteps(L, nodesAt(L));
        }
        const r = gen.next();
        if (r.done) { cache.set(h, r.value); gen = null; sceneRef.current?.setAttribute("data-lines-ready", [...cache.keys()].join(",")); }
      }
      if (!cancelled && (gen || todo.length)) id = requestIdleCallback(work, { timeout: IDLE_TIMEOUT_MS });
    };
    id = requestIdleCallback(work, { timeout: IDLE_TIMEOUT_MS });
    return () => { cancelled = true; cancelIdleCallback(id); };
  }, [base, geo?.cRaw]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={sceneRef} className="absolute inset-x-0 bottom-0 overflow-hidden bg-bg-0" style={geo ? { height: geo.h } : { top: 0 }} data-home>
      <div aria-hidden="true" className="pointer-events-none absolute inset-0" style={{ background: "radial-gradient(60% 50% at 50% 47%, color-mix(in oklab, var(--color-gold) 6%, transparent), transparent 70%)" }} />
      {/* the stage and the canvas wrapper are their own compositor layers from the start (will-change), so the first banner's
          glide doesn't create and upload a full-size layer mid-animation (a dropped frame at 1440 px) */}
      <motion.div ref={stageRef} className="absolute inset-0 will-change-transform" style={{ x: fx, y: fy, scale: fs, transformOrigin: origin }} data-home-stage>
      {/* the canvas keeps the banner-free layout and is moved by the banners' exact offset (shiftLayout moves the ink by
          the same amount), so a banner never re-creates it: no canvas re-allocation, no frame lost */}
      <motion.div className="absolute inset-0" style={{ opacity: wordO, scale: wordS, transformOrigin: `50% ${layout ? layout.cy : 0}px` }} data-home-word>
      <div className="absolute inset-0 will-change-transform" style={{ transform: `translateY(${layout && base ? layout.cy - base.cy : 0}px)` }}>
        {/* a return shows the wordmark drawn (its first-open intro only plays once a session) */}
        <FlowTitle skip={skip || ret} still={calm} onSettled={onSettled} layout={base} />
      </div>
      </motion.div>
      </motion.div>

      <svg className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
        {showNodes && nodes.map((n) => {
          const d = lineGeo![n.id].d;
          const lit = hover === n.id;
          const here = arrived > ARRIVAL_ORDER.indexOf(n.id);
          const instant = calm || skip;
          return (
            <g key={n.id} data-line={n.id}>
              {calm ? (
                // Calm: a plain line, drawn in the very first frame
                <path d={d} fill="none" className={lit ? "stroke-gold" : "stroke-gold/30"} strokeWidth={lit ? 1.3 : 1} opacity={n.disabled ? 0.35 : 1} />
              ) : (
              <motion.path key={skip ? "done" : "in"} d={d} fill="none" className={cn(lit ? "stroke-gold" : "stroke-gold/30", LINE_T)} strokeWidth={lit ? 1.3 : 1}
                initial={{ pathLength: instant && here ? 1 : 0, opacity: instant && here ? (n.disabled ? 0.35 : 1) : 0 }}
                animate={{ pathLength: here ? 1 : 0, opacity: here ? (n.disabled ? 0.35 : 1) : 0 }}
                transition={{ duration: instant ? 0 : ret ? RET_LINE_S : LINE_DRAW_S, delay: instant || ret ? 0 : LINE_DELAY_S, ease: [0.3, 0, 0, 1] }} />
              )}
              {!calm && !n.disabled && (
                <path d={d} fill="none" className={cn("cf-flowdash stroke-cream/55", lit && "cf-flowdash-lit")} strokeWidth={1.4} strokeDasharray="2 22" strokeLinecap="round"
                  style={{ opacity: phase === "idle" ? 1 : 0, transition: "opacity .6s" }} />
              )}
            </g>
          );
        })}
      </svg>

      {/* nodes (owner 2026-11-15): the header is fixed on its point and never moves; the hover card is a separate overlay
          next to it, towards the wordmark (top nodes below, bottom nodes above), kept inside the scene, opaque and above
          every line. Nothing here uses layout animation, so a re-render (status, clock) can never shift a node. */}
      {showNodes && nodes.map((n) => {
        const Icon = n.icon;
        const lit = hover === n.id;
        const here = arrived > ARRIVAL_ORDER.indexOf(n.id);
        const instant = calm || skip;
        const [px, py] = pointOf(n);
        const down = n.slot.startsWith("top");
        const shift = cardShift(px);
        return (
          <div key={n.id} className={cn("absolute size-0", lit ? "z-30" : "z-10")} style={{ left: px, top: py }} data-home-node={n.id} data-glide={n.id}
            onMouseEnter={() => here && setHover(n.id)} onMouseLeave={() => setHover(null)}>
            <motion.button key={skip ? "done" : "in"}
              onFocus={() => setHover(n.id)} onBlur={() => setHover(null)}
              onClick={() => open(n)}
              aria-label={`${n.name} (key ${n.key})${n.disabled ? ", not available yet" : ""}`}
              initial={instant && here ? { opacity: 1, scale: 1 } : { opacity: 0, scale: 0.85 }}
              animate={here ? { opacity: 1, scale: 1 } : { opacity: 0, scale: 0.85 }}
              transition={{ duration: instant ? 0 : ret ? RET_NODE_S : NODE_IN_S, ease: [0, 0, 0.2, 1] }}
              tabIndex={here ? 0 : -1}
              className="absolute left-0 top-0 -translate-x-1/2 -translate-y-1/2 rounded-lg text-left"
              style={{ pointerEvents: here ? "auto" : "none" }}
            >
              {/* the label lights up as one: border, background, glow, the icon and its circle, the number badge (and a
                  dimmed node's strength) all change in the same frame on one 180-ms ease-out transition, and fade back
                  the same way; Calm: at once (owner 2026-11-15) */}
              <span data-node-label={lit ? "lit" : "idle"}
                className={cn("flex h-11 items-center gap-2.5 whitespace-nowrap rounded-lg border bg-clip-padding px-3.5 backdrop-blur-md", !calm && LABEL_T,
                  lit ? "border-gold/50 bg-bg-2 shadow-[0_0_28px_color-mix(in_oklab,var(--color-gold)_18%,transparent)]" : "border-white/[.08] bg-bg-2/60 shadow-[0_0_28px_transparent]",
                  n.disabled && !lit ? "opacity-55" : "opacity-100")}>
                <span className={cn("grid size-7 place-items-center rounded-full border", !calm && LABEL_T, lit ? "border-gold/60 bg-gold/15 text-gold" : "border-white/10 bg-white/[.04] text-cream")}>
                  <Icon className="size-4" strokeWidth={1.5} aria-hidden="true" />
                </span>
                <span className={cn("text-[14px] font-medium", !calm && LABEL_T, lit ? "text-cream" : "text-text-1")}>{n.name}</span>
                <kbd className={cn("ml-1 rounded-xs border px-1.5 font-mono text-[10px]", !calm && LABEL_T, lit ? "border-gold/50 text-gold" : "border-line-strong text-text-3")}>{n.key}</kbd>
              </span>
            </motion.button>
            <AnimatePresence>
              {lit && (
                // the transparent band between the header and the card keeps the pointer "inside" while it crosses over
                // opens with a short fade and a slight scale-up from the node towards the wordmark (180 ms, ease-out),
                // leaves faster (120 ms); opacity and transform only; Calm: no motion (owner 2026-11-15)
                <motion.div key="card" onClick={() => open(n)} data-home-card={n.id}
                  className={cn("absolute", down ? "pt-1.5" : "pb-1.5")}
                  style={{ left: shift, width: CARD_W, x: "-50%", transformOrigin: `${CARD_W / 2 - shift}px ${down ? "0%" : "100%"}`,
                    ...(down ? { top: HEADER_H / 2 } : { bottom: HEADER_H / 2 }) }}
                  initial={calm ? { opacity: 1 } : { opacity: 0, scale: CARD_SCALE_FROM, y: down ? -6 : 6 }}
                  animate={{ opacity: 1, scale: 1, y: 0, transition: { duration: calm ? 0 : CARD_IN_S, ease: [0, 0, 0.2, 1] } }}
                  exit={calm ? { opacity: 0, transition: { duration: 0 } }
                    : { opacity: 0, scale: 0.97, y: down ? -3 : 3, transition: { duration: CARD_OUT_S, ease: [0.4, 0, 1, 1] } }}>
                  {/* its own hover: a 2-px lift and a soft cream edge glow (a separate layer whose opacity changes) */}
                  <div className={cn("group/card relative cursor-pointer rounded-lg", !calm && "transition-transform duration-200 ease-out hover:-translate-y-0.5")} data-home-card-body>
                    <span aria-hidden="true" data-home-card-glow
                      className={cn("pointer-events-none absolute -inset-px rounded-lg opacity-0 shadow-[0_0_0_1px_color-mix(in_oklab,var(--color-cream)_45%,transparent),0_0_24px_color-mix(in_oklab,var(--color-cream)_20%,transparent)] group-hover/card:opacity-100",
                        !calm && "transition-opacity duration-200")} />
                    <div className="relative rounded-lg border border-gold/50 bg-bg-2 px-3.5 py-3 shadow-[0_12px_32px_rgba(0,0,0,.55)]">
                      <div className="eyebrow">{n.label}</div>
                      <div className="mt-1.5 font-mono text-[22px] leading-7 text-text-1">{n.value}</div>
                      <div className="mt-[7px] text-[12px] leading-4 text-text-3">{n.note}</div>
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}


      {/* messages: one row in the bottom-right corner on the date block's line (both 44 px tall, 16 px from the bottom
          edge; owner 2026-11-15) */}
      <div className="pointer-events-none absolute bottom-4 right-6 z-20 flex h-11 flex-row items-center gap-3" data-home-messages>
        <AnimatePresence>
          {toast && (
            <motion.div role="status" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: calm ? 0 : 0.2 }}
              className="rounded-md border border-line-strong bg-bg-3 px-4 py-2 text-[13px] text-text-1 shadow-[0_12px_32px_rgba(0,0,0,.55)]">
              {toast}
            </motion.div>
          )}
        </AnimatePresence>
        <div className="font-mono text-[11px] text-text-3">
          {play && phase !== "idle" ? "click or press any key to skip" : "1–6 open a page · Ctrl K search"}
        </div>
      </div>
    </div>
  );
}
