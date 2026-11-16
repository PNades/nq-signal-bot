// The lifecycle chart (owner 2026-11-15): one SVG chart for every Monte Carlo fan and Drawdown view, so they all read
// the same way: the account balance through its stages, each stage with its own floor, the evaluation target only while
// in an evaluation, the payout line only while funded. Stage markers (passed, payout, failed) are small HTML dots with a
// hover / focus tooltip, kept inside the plot. Zoom (rework 4): the scroll wheel zooms the time axis around the
// pointer, a drag pans it, a double-click resets; the y-axis fits the visible days; markers and the floor label stay
// inside the plot at every zoom.
// From 21st.dev (code read 2026-11-15, no network calls, nothing embedded): the tooltip body after "Chart Tooltip"
// (uiable, id 26600), the marker + hover card after "MarkerTooltip" (mapcn, id 13982), the header after "Market Card"
// (louisstudios, id 45580), zoom and pan after "Range Navigator" (ssychui, id 23954) and "Brush Chart" (kuratlielia,
// id 35059): drag the window, pull to zoom, a "zoomed" readout; adapted to MASTER.md tokens. The draw-out (DRAW_S,
// click or any key skips, Calm draws it whole) as before.
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, Info, X } from "lucide-react";
import { money, money0 } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { LifeEvent, LifeKind, McFan, StageName } from "./types";

const DRAW_S = 1.25;
const easeOut = (t: number) => 1 - (1 - t) ** 3;
const PAD_R = 78;  // the y-axis labels
const PAD_B = 20;  // the x-axis labels
const W = 1000, H = 300, P = 10;
const EDGE = 12;   // markers and labels stay this far inside the plot
const MIN_SPAN = 5; // trading days at the deepest zoom

function niceStep(raw: number) {
  const p = 10 ** Math.floor(Math.log10(raw || 1)), f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

export interface LifeSeries {
  balance: number[]; floor: (number | null)[]; target: (number | null)[]; payout_line: (number | null)[]; stage: StageName[];
}
export interface Marker { at: number; event: LifeEvent }

// ── the chart header and legend (shared by every view) ──
export function ChartHeader({ kicker, title, right, hint }: { kicker: string; title: ReactNode; right?: ReactNode; hint?: string }) {
  return (
    <div className="flex h-[64px] items-end gap-4" data-chart-header>
      <div className="min-w-0 flex-1">
        <div className="eyebrow flex min-w-0 items-center gap-1.5"><span className="truncate" data-hint={kicker}>{kicker}</span>{hint && <Info className="size-3 shrink-0 text-text-3" aria-label={hint} data-hint={hint} tabIndex={0} />}</div>
        <div className="mt-1 truncate font-display text-[22px] leading-7 text-text-1">{title}</div>
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  );
}

export type Swatch = "band-outer" | "band-inner" | "median" | "base" | "balance" | "floor" | "floor-base" | "target" | "payout" | "payout-marker" | LifeKind;
const LEGEND: Record<Swatch, string> = {
  "band-outer": "5–95%", "band-inner": "25–75%", median: "median", base: "backtest order", balance: "balance",
  floor: "floor", "floor-base": "floor (backtest order)", target: "evaluation target", payout: "payout line",
  "payout-marker": "payout", passed: "passed", failed: "failed", capped: "cap reached",
};
const LEGEND_HINT: Partial<Record<Swatch, string>> = {
  base: "The strategy's backtest days played forward in their original order, through the same rules: one path among the 1,000",
  "floor-base": "The floor along the backtest-order path. Every path has its own trailing floor (it follows that path's own high), so this line belongs to the backtest-order path only",
  floor: "The floor in force at each session end (§6.7): trailing the end-of-day high, locked where the rules lock it",
  target: "Start + profit target: shown only while the account is in an evaluation",
  payout: "The balance at which a payout can be requested (the buffer plus the minimum): shown only while funded",
  "band-outer": "5% to 95% of the 1,000 paths' balances on each day", "band-inner": "25% to 75% of the paths' balances",
  median: "The median of the paths' balances on each day",
};
function SwatchIcon({ k }: { k: Swatch }) {
  switch (k) {
    case "band-outer": return <span className="h-2 w-3.5 rounded-xs bg-gold/15" />;
    case "band-inner": return <span className="h-2 w-3.5 rounded-xs bg-gold/30" />;
    case "median": return <span className="h-0.5 w-3.5 rounded-full bg-cream" />;
    case "base": return <span className="h-0.5 w-3.5 rounded-full bg-orb-pika" />;
    case "balance": return <span className="h-0.5 w-3.5 rounded-full bg-cream" />;
    case "floor": case "floor-base": return <span className="h-0 w-3.5 border-t border-dashed border-crit" />;
    case "target": return <span className="h-0 w-3.5 border-t border-dashed border-ok" />;
    case "payout": return <span className="h-0 w-3.5 border-t border-dashed border-ember" />;
    case "payout-marker": return <MarkerGlyph kind="payout" small />;
    default: return <MarkerGlyph kind={k} small />;
  }
}
export function Legend({ items }: { items: Swatch[] }) {
  return (
    <ul className="flex min-h-11 flex-wrap content-start items-center gap-x-3.5 gap-y-1 font-mono text-[11px] text-text-3 min-[1300px]:min-h-5" data-legend>
      {items.map((k) => (
        <li key={k} className={cn("flex items-center gap-1.5", LEGEND_HINT[k] && "cursor-help underline decoration-dotted decoration-text-3/50 underline-offset-[3px]")}
          data-hint={LEGEND_HINT[k]} tabIndex={LEGEND_HINT[k] ? 0 : undefined} data-legend-item={k}>
          <SwatchIcon k={k} />{LEGEND[k]}
        </li>
      ))}
    </ul>
  );
}

// ── markers ──
export function MarkerGlyph({ kind, small = false }: { kind: LifeKind; small?: boolean }) {
  const s = small ? "size-2.5" : "size-3";
  if (kind === "payout") return <span className={cn("block rotate-45 rounded-[2px] bg-gold shadow-[0_0_0_2px_var(--color-bg-1)]", small ? "size-2" : "size-2.5")} />;
  if (kind === "passed") return <span className={cn("grid place-items-center rounded-full bg-ok shadow-[0_0_0_2px_var(--color-bg-1)]", s)}><Check className="size-2 text-bg-0" strokeWidth={4} aria-hidden="true" /></span>;
  if (kind === "failed") return <span className={cn("grid place-items-center rounded-full bg-crit shadow-[0_0_0_2px_var(--color-bg-1)]", s)}><X className="size-2 text-bg-0" strokeWidth={4} aria-hidden="true" /></span>;
  return <span className={cn("block rounded-[2px] bg-info shadow-[0_0_0_2px_var(--color-bg-1)]", s)} />;
}

function eventLines(e: LifeEvent, splitPct: number | null): { title: string; rows: [string, string, string?][] } {
  const bal = e.balance !== undefined ? money(e.balance, false) : "—";
  if (e.kind === "passed") return { title: "Evaluation passed", rows: [["Trading days", String(e.days ?? "—")], ["Balance", bal]] };
  if (e.kind === "payout") {
    const rows: [string, string, string?][] = [["Amount (gross)", money(e.amount ?? 0, false), "text-gold"]];
    if (splitPct !== null && splitPct < 1) rows.push([`Your share (${Math.round(splitPct * 100)}%)`, money((e.amount ?? 0) * splitPct, false)]);
    rows.push(["Balance before", bal]);
    return { title: `Payout ${e.n ?? ""}`.trim(), rows };
  }
  if (e.kind === "failed") return { title: `Floor reached · ${e.stage === "funded" ? "funded account lost" : "evaluation failed"}`, rows: [["Balance", bal, "text-pnl-down"]] };
  return { title: "Payout cap reached", rows: [["Next", "the account closes"]] };
}

/** A tooltip kept fully inside its box: above the anchor if it fits, else below; clamped left and right. */
function ClampedTip({ anchor, box, children }: { anchor: { x: number; y: number }; box: { w: number; h: number }; children: ReactNode }) {
  const el = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  useLayoutEffect(() => {
    const t = el.current;
    if (!t) return;
    const tw = t.offsetWidth, th = t.offsetHeight, m = 6, gap = 12;
    const left = Math.max(m, Math.min(box.w - tw - m, anchor.x - tw / 2));
    let top = anchor.y - th - gap;
    if (top < m) top = anchor.y + gap;
    top = Math.max(m, Math.min(box.h - th - m, top));
    setPos((p) => (p && p.left === left && p.top === top ? p : { left, top }));
  });
  return (
    <div ref={el} role="tooltip" data-chart-tip
      className="pointer-events-none absolute z-30 grid min-w-[10rem] gap-1.5 rounded-lg border border-line-strong/80 bg-bg-3/95 px-2.5 py-2 text-[12px] shadow-[0_12px_32px_rgba(0,0,0,.55)] backdrop-blur-sm"
      style={{ left: pos?.left ?? 0, top: pos?.top ?? 0, visibility: pos ? "visible" : "hidden" }}>
      {children}
    </div>
  );
}

function TipBody({ title, rows, dot }: { title: string; rows: [string, string, string?][]; dot?: ReactNode }) {
  return (
    <>
      <div className="flex items-center gap-2 font-medium text-text-1">{dot}{title}</div>
      <div className="grid gap-1">
        {rows.map(([k, v, cls]) => (
          <div key={k} className="flex items-baseline justify-between gap-5 leading-none">
            <span className="text-text-2">{k}</span><span className={cn("font-mono tabular-nums", cls ?? "text-text-1")}>{v}</span>
          </div>
        ))}
      </div>
    </>
  );
}

// ── the chart ──
export function LifecycleChart({ series, markers, bands, extras, xLabel, xTick, tickAt, animated, splitPct, height = 340, floorLabel = "floor" }: {
  series: LifeSeries; markers: Marker[];
  /** the Monte Carlo fan's bands (absent on Drawdown) */
  bands?: McFan["bands"];
  /** extra lines over the fan: the median path */
  extras?: { median?: number[] };
  /** the tooltip's label for an index, and the x-axis tick label */
  xLabel: (i: number) => string; xTick: (i: number) => string;
  /** where the x-axis ticks go (a per-trade series: each trading day's first point); default: evenly by index */
  tickAt?: number[];
  animated: boolean; splitPct: number | null; height?: number;
  /** the floor line's own label at the plot's right edge */
  floorLabel?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const clip = useRef<SVGRectElement>(null);
  const clipId = useId().replace(/:/g, "");
  const [done, setDone] = useState(!animated);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hoverI, setHoverI] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);   // a marker hovered or focused
  const n = series.balance.length;
  const full: [number, number] = [0, Math.max(1, n - 1)];
  const [view, setView] = useState<[number, number]>(full);
  const drag = useRef<{ x: number; v: [number, number] } | null>(null);
  useEffect(() => { setView([0, Math.max(1, n - 1)]); }, [series, n]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!animated) { clip.current?.setAttribute("width", String(W)); setDone(true); return; }
    let raf = 0, t0 = 0, stopped = false;
    setDone(false);
    const finish = () => { if (stopped) return; stopped = true; cancelAnimationFrame(raf); clip.current?.setAttribute("width", String(W)); setDone(true); };
    const frame = (now: number) => {
      if (!t0) t0 = now;
      const k = Math.min(1, (now - t0) / (DRAW_S * 1000));
      clip.current?.setAttribute("width", String(P + easeOut(k) * (W - P)));
      if (k < 1) raf = requestAnimationFrame(frame); else finish();
    };
    clip.current?.setAttribute("width", String(P));
    raf = requestAnimationFrame(frame);
    const skip = () => finish();
    const el = box.current;
    el?.addEventListener("pointerdown", skip);
    window.addEventListener("keydown", skip);
    return () => { stopped = true; cancelAnimationFrame(raf); el?.removeEventListener("pointerdown", skip); window.removeEventListener("keydown", skip); };
  }, [series, bands, animated]);

  const plotW = Math.max(0, size.w - PAD_R), plotH = Math.max(0, size.h - PAD_B);
  const [v0, v1] = view;
  const span = Math.max(1e-6, v1 - v0);
  const zoomed = v0 > 0.001 || v1 < full[1] - 0.001;

  // the wheel zooms around the pointer (a non-passive listener, so the page doesn't scroll under the chart)
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (n < MIN_SPAN + 2) return;
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const fx = Math.max(0, Math.min(1, (e.clientX - r.left) / Math.max(1, el.clientWidth - PAD_R)));
      setView(([a, b]) => {
        const s = b - a, at = a + ((fx * W - P) / (W - 2 * P)) * s;
        const ns = Math.max(MIN_SPAN, Math.min(full[1], s * (e.deltaY > 0 ? 1.18 : 1 / 1.18)));
        let na = at - ((at - a) / s) * ns;
        na = Math.max(0, Math.min(full[1] - ns, na));
        return [na, na + ns];
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [n, full[1]]); // eslint-disable-line react-hooks/exhaustive-deps

  const lo_i = Math.max(0, Math.floor(v0)), hi_i = Math.min(n - 1, Math.ceil(v1));
  const vis = <T,>(arr: T[]) => arr.slice(lo_i, hi_i + 1);
  // where one account ends and the next starts (a pass or a floor): lines never join across it
  const breaks = useMemo(() => new Set(markers.filter((m) => m.event.kind === "passed" || m.event.kind === "failed").map((m) => m.at)), [markers]);
  const vals = [...vis(series.balance), ...(vis(series.floor).filter((v) => v !== null) as number[]), ...(vis(series.target).filter((v) => v !== null) as number[]),
    ...(vis(series.payout_line).filter((v) => v !== null) as number[]), ...(bands ? vis(bands).flatMap((b) => [b.p5, b.p95]) : [])];
  const lo0 = Math.min(...vals), hi0 = Math.max(...vals);
  const padY = (hi0 - lo0 || 1) * 0.08;
  const lo = lo0 - padY, hi = hi0 + padY;
  const x = (i: number) => P + ((i - v0) / span) * (W - 2 * P);
  const y = (v: number) => P + (1 - (v - lo) / (hi - lo || 1)) * (H - 2 * P);
  /** a path through the non-null values, broken at nulls and (if `cut`) account changes; `step` draws steps */
  const segs = (arr: (number | null)[], step = false, cut = true) => {
    let d = "", prev: number | null = null;
    arr.forEach((v, i) => {
      if (v === null) { prev = null; return; }
      if (prev === null || (cut && breaks.has(i - 1))) d += ` M${x(i)},${y(v)}`;
      else d += step ? ` L${x(i)},${y(prev)} L${x(i)},${y(v)}` : ` L${x(i)},${y(v)}`;
      prev = v;
    });
    return d.trim();
  };
  const band = (a: "p95" | "p75", b: "p5" | "p25") => bands ?
    `M${bands.map((q, i) => `${x(i)},${y(q[a])}`).join(" L")} L${bands.map((_, i) => `${x(bands.length - 1 - i)},${y(bands[bands.length - 1 - i][b])}`).join(" L")} Z` : "";
  const ticks: number[] = [];
  const st = niceStep((hi - lo) / 5);
  for (let v = Math.ceil(lo / st) * st; v <= hi; v += st) ticks.push(v);
  const stepX = Math.max(1, niceStep(span / 6));
  const xTicks: number[] = [];
  if (tickAt) {
    const vis = tickAt.filter((i) => i >= v0 - 1e-6 && i <= v1 + 1e-6);
    const every = Math.max(1, Math.ceil(vis.length / 7));
    vis.forEach((i, k) => { if (k % every === 0) xTicks.push(i); });
  } else {
    for (let i = Math.ceil(v0 / stepX) * stepX; i <= v1 + 1e-6; i += stepX) xTicks.push(Math.round(i));
  }
  const px = (i: number) => (x(i) / W) * plotW, py = (v: number) => (y(v) / H) * plotH;
  const inX = (v: number) => Math.max(EDGE, Math.min(plotW - EDGE, v)), inY = (v: number) => Math.max(EDGE, Math.min(plotH - EDGE, v));
  const shown = markers.map((m, k) => ({ m, k })).filter(({ m }) => m.at >= v0 - 1e-6 && m.at <= v1 + 1e-6);

  const onMove = (e: React.PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    if (drag.current) {
      const d = ((e.clientX - drag.current.x) / Math.max(1, plotW)) * (drag.current.v[1] - drag.current.v[0]);
      const [a, b] = drag.current.v, s = b - a;
      const na = Math.max(0, Math.min(full[1] - s, a - d));
      setView([na, na + s]);
      return;
    }
    if (!done || pinned !== null) return;
    const fx = (e.clientX - r.left) / Math.max(1, plotW);
    if (fx < 0 || fx > 1) return setHoverI(null);
    setHoverI(Math.round(v0 + ((fx * W - P) / (W - 2 * P)) * span));
  };
  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0 || (e.target as Element).closest("[data-marker]") || !zoomed) return;
    drag.current = { x: e.clientX, v: view };
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
  };
  const onUp = () => { drag.current = null; };
  const hov = hoverI === null ? null : Math.max(lo_i, Math.min(hi_i, hoverI));
  const pinnedMarker = pinned === null ? null : markers[pinned];
  // the floor's own label at the right edge of the visible days
  let floorAt: number | null = null;
  for (let i = hi_i; i >= lo_i; i--) if (series.floor[i] !== null) { floorAt = i; break; }

  return (
    <div ref={box} className={cn("relative select-none", zoomed ? "cursor-grab active:cursor-grabbing" : "cursor-crosshair")} style={{ height, paddingRight: PAD_R, paddingBottom: PAD_B }}
      onPointerMove={onMove} onPointerDown={onDown} onPointerUp={onUp} onPointerCancel={onUp} onPointerLeave={() => setHoverI(null)}
      onDoubleClick={() => setView(full)} data-lifecycle-chart data-drawn={done ? "1" : "0"} data-zoomed={zoomed ? "1" : "0"}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block size-full overflow-hidden" role="img" aria-label="Account balance over the trading days">
        <defs>
          <clipPath id={clipId}><rect ref={clip} x={0} y={0} width={animated ? P : W} height={H} /></clipPath>
          <clipPath id={`${clipId}-plot`}><rect x={P} y={0} width={W - 2 * P} height={H} /></clipPath>
        </defs>
        {ticks.map((v) => <line key={v} x1={P} x2={W - P} y1={y(v)} y2={y(v)} stroke="var(--color-line-chart)" strokeWidth={1} vectorEffect="non-scaling-stroke" />)}
        <g clipPath={`url(#${clipId}-plot)`}><g clipPath={`url(#${clipId})`}>
          {bands && <path d={band("p95", "p5")} fill="rgba(255,224,194,.09)" />}
          {bands && <path d={band("p75", "p25")} fill="rgba(255,224,194,.16)" />}
          {extras?.median && <path d={segs(extras.median, false, false)} fill="none" stroke="var(--color-cream)" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />}
          <path d={segs(series.target)} fill="none" stroke="var(--color-ok)" strokeDasharray="4 5" strokeWidth={1} vectorEffect="non-scaling-stroke" data-line="target" />
          <path d={segs(series.payout_line)} fill="none" stroke="var(--color-ember)" strokeDasharray="4 5" strokeWidth={1} opacity={0.9} vectorEffect="non-scaling-stroke" data-line="payout" />
          <path d={segs(series.floor, true)} fill="none" stroke="var(--color-crit)" strokeDasharray="6 4" strokeWidth={1} vectorEffect="non-scaling-stroke" data-line="floor" />
          <path d={segs(series.balance.map((v, i) => (i > 0 && series.stage[i] === "ended" && series.stage[i - 1] === "ended" ? null : v)))} fill="none" stroke={bands ? "var(--color-orb-pika)" : "var(--color-cream)"} strokeWidth={bands ? 1.1 : 1.6} opacity={bands ? 0.75 : 1} vectorEffect="non-scaling-stroke" data-line="balance" />
        </g></g>
        {hov !== null && pinned === null && !drag.current && <line x1={x(hov)} x2={x(hov)} y1={P} y2={H - P} stroke="rgba(255,224,194,.35)" strokeDasharray="3 4" strokeWidth={1} vectorEffect="non-scaling-stroke" />}
      </svg>
      {ticks.map((v) => (
        <span key={v} className="pointer-events-none absolute right-0 -translate-y-1/2 font-mono text-[11px] tabular-nums text-text-3" style={{ top: py(v) }}>{money0(v)}</span>
      ))}
      {plotW > 0 && xTicks.map((i) => (
        <span key={i} className="pointer-events-none absolute bottom-0 -translate-x-1/2 whitespace-nowrap font-mono text-[11px] text-text-3" style={{ left: Math.max(24, Math.min(plotW - 24, px(i))) }}>{xTick(i)}</span>
      ))}
      {/* the floor's label at the right edge of the visible days, inside the plot */}
      {plotW > 0 && floorAt !== null && done && (
        <span className={cn("pointer-events-none absolute z-[5] whitespace-nowrap rounded-xs border border-crit/40 bg-bg-1/90 px-1.5 font-mono text-[10.5px] leading-4 text-crit",
          px(floorAt) > plotW / 2 ? "-translate-x-full" : "")}
          style={{ left: px(floorAt) > plotW / 2 ? Math.min(plotW - 6, px(floorAt)) : Math.max(6, px(floorAt) + 6), top: Math.max(4, Math.min(plotH - 20, py(series.floor[floorAt] as number) + 4)) }} data-floor-label>{floorLabel} {money0(series.floor[floorAt] as number)}</span>
      )}
      {/* the stage markers, inside the plot area (EDGE px from every edge) */}
      {plotW > 0 && shown.map(({ m, k }) => (
        <button key={`${m.at}-${m.event.kind}-${k}`} type="button" aria-label={`${eventLines(m.event, splitPct).title}, ${xLabel(m.at)}`}
          data-marker={m.event.kind} onPointerEnter={() => setPinned(k)} onPointerLeave={() => setPinned(null)} onFocus={() => setPinned(k)} onBlur={() => setPinned(null)}
          className={cn("absolute z-10 -translate-x-1/2 -translate-y-1/2 rounded-full p-1 outline-none transition-[opacity,transform] duration-200 hover:scale-125 focus-visible:ring-2 focus-visible:ring-cream",
            done ? "opacity-100" : "pointer-events-none opacity-0")}
          style={{ left: inX(px(m.at)), top: inY(py(series.balance[m.at])) }}>
          <MarkerGlyph kind={m.event.kind} />
        </button>
      ))}
      {pinnedMarker && (
        <ClampedTip anchor={{ x: inX(px(pinnedMarker.at)), y: inY(py(series.balance[pinnedMarker.at])) }} box={size}>
          <TipBody title={eventLines(pinnedMarker.event, splitPct).title} rows={[["When", xLabel(pinnedMarker.at)], ...eventLines(pinnedMarker.event, splitPct).rows]}
            dot={<MarkerGlyph kind={pinnedMarker.event.kind} small />} />
        </ClampedTip>
      )}
      {!pinnedMarker && hov !== null && !drag.current && (
        <ClampedTip anchor={{ x: px(hov), y: py(series.balance[hov]) }} box={size}>
          <TipBody title={xLabel(hov)} rows={[
            ["Stage", series.stage[hov] ?? "—"],
            ["Balance", money(series.balance[hov], false)],
            ...(series.floor[hov] !== null ? [["Floor", money(series.floor[hov] as number, false), "text-crit"] as [string, string, string]] : []),
            ...(series.target[hov] !== null ? [["Target", money(series.target[hov] as number, false), "text-ok"] as [string, string, string]] : []),
            ...(series.payout_line[hov] !== null ? [["Payout line", money(series.payout_line[hov] as number, false), "text-ember"] as [string, string, string]] : []),
            ...(bands ? [["Median of paths", money(bands[hov].p50, false)] as [string, string], ["5–95%", `${money0(bands[hov].p5)} – ${money0(bands[hov].p95)}`] as [string, string]] : []),
          ]} />
        </ClampedTip>
      )}
      <span className="pointer-events-none absolute left-1 top-1 font-mono text-[10.5px] text-text-3" data-zoom-hint>
        {!done ? "click or press a key to skip" : zoomed ? `${xTick(Math.round(v0))} – ${xTick(Math.round(v1))} · drag to pan · double-click to reset` : "scroll to zoom"}</span>
    </div>
  );
}
