// Performance charts (design-system/pages/performance.md, MASTER.md §11), from the step-14a mock, on the API's data:
// equity and drawdown on TradingView Lightweight Charts (npm, self-hosted, attribution logo kept), the Monte Carlo fan
// and live vs expected as SVG. Crosshair tooltips styled to the tokens.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { AreaSeries, ColorType, CrosshairMode, LineSeries, LineStyle, LineType, TickMarkType, createChart, createSeriesMarkers, type AutoscaleInfo, type IChartApi, type SeriesMarker, type Time } from "lightweight-charts";
import { money, rr } from "@/lib/format";
import type { Band, EquityPoint, McFan, PhaseStart, Run, Unit } from "./types";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmtDate = (d: string) => new Date(d + "T12:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

function niceStep(raw: number) {
  const p = 10 ** Math.floor(Math.log10(raw || 1)), f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

/** autoSize keeps the bar spacing when the box changes size: fit the whole range again on every resize. */
function fitOnResize(el: HTMLElement, chart: IChartApi) {
  let last = el.clientWidth;
  const ro = new ResizeObserver(() => {
    if (el.clientWidth === last) return;
    last = el.clientWidth;
    requestAnimationFrame(() => chart.timeScale().fitContent());
  });
  ro.observe(el);
  return () => ro.disconnect();
}

function baseChart(el: HTMLElement): IChartApi {
  return createChart(el, {
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--color-text-3"), fontFamily: "Geist Mono Variable, monospace", fontSize: 11, attributionLogo: true },
    grid: { vertLines: { visible: false }, horzLines: { color: css("--color-line-chart") } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false },
    crosshair: {
      mode: CrosshairMode.Magnet,
      vertLine: { color: "rgba(255,224,194,.4)", style: LineStyle.Dashed, labelBackgroundColor: css("--color-bg-3") },
      horzLine: { color: "rgba(255,224,194,.4)", style: LineStyle.Dashed, labelBackgroundColor: css("--color-bg-3") },
    },
    handleScroll: false, handleScale: false,
  });
}

type Tip = { x: number; y: number; date: string; lines: [string, string, string?][] } | null;

function Tooltip({ tip }: { tip: Tip }) {
  if (!tip) return null;
  return (
    <div className="pointer-events-none absolute z-20 min-w-[160px] rounded-sm border border-line-strong bg-bg-3 px-3 py-2 shadow-[0_12px_32px_rgba(0,0,0,.55)]"
      style={{ left: tip.x + 14, top: Math.max(8, tip.y - 70) }}>
      <div className="eyebrow">{tip.date}</div>
      {tip.lines.map(([k, v, cls]) => (
        <div key={k} className="mt-1 flex items-baseline justify-between gap-4">
          <span className="text-[12px] text-text-2">{k}</span><span className={`font-mono text-[13px] ${cls ?? "text-text-1"}`}>{v}</span>
        </div>
      ))}
    </div>
  );
}

const PHASE_COLOR: Record<string, string> = { development: "--color-gold", demo: "--color-info", prop: "--color-ok", live: "--color-ok" };

/** Equity on one time axis: each phase in its own colour, a marker where it starts (SPEC §9b item 6). */
const DAY_TICK = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
/** the price scale always includes 0 (the curve's start), never zooms into one value (owner 2026-11-16) */
const withZero = (orig: () => AutoscaleInfo | null): AutoscaleInfo | null => {
  const r = orig();
  if (!r?.priceRange) return r;
  const lo = Math.min(0, r.priceRange.minValue), hi = Math.max(0, r.priceRange.maxValue);
  const pad = (hi - lo || 1) * 0.06;
  return { ...r, priceRange: { minValue: lo - (lo < 0 ? pad : 0), maxValue: hi + (hi > 0 ? pad : 0) + (hi === lo ? 1 : 0) } };
};

export function EquityChart({ points, starts, unit, animated, perTrade = false }: { points: EquityPoint[]; starts: PhaseStart[]; unit: Unit; animated: boolean; perTrade?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || !points.length) return;
    const chart = baseChart(el);
    if (perTrade) {
      // one point per trade at its exit time: each trading day labelled once (at its first point), no intraday ticks
      const firstOfDay = new Set(points.filter((p, i) => !i || p.date !== points[i - 1].date).map((p) => p.time));
      chart.applyOptions({
        timeScale: { timeVisible: false, tickMarkFormatter: (t: Time, type: TickMarkType) => (type <= TickMarkType.DayOfMonth && firstOfDay.has(t as number) ? DAY_TICK.format(new Date((t as number) * 1000)) : "") },
        localization: { timeFormatter: (t: Time) => { const d = new Date((t as number) * 1000); return `${DAY_TICK.format(d)} ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`; } },
      });
    }
    const fmt = unit === "usd" ? (v: number) => money(v, false) : (v: number) => rr(v);
    // one series per phase, each continuing from the previous phase's last point
    const phases = [...new Set(points.map((p) => p.phase))];
    const series = phases.map((ph, k) => {
      const color = css(PHASE_COLOR[ph] ?? "--color-cream");
      const s = k === 0
        ? chart.addSeries(AreaSeries, { lineColor: color, lineWidth: 2, topColor: "rgba(255,224,194,.16)", bottomColor: "rgba(255,224,194,0)",
            priceFormat: { type: "custom", formatter: fmt }, lastValueVisible: phases.length === 1, priceLineVisible: false, autoscaleInfoProvider: withZero })
        : chart.addSeries(LineSeries, { color, lineWidth: 2, priceFormat: { type: "custom", formatter: fmt }, lastValueVisible: true, priceLineVisible: false, autoscaleInfoProvider: withZero });
      const first = points.findIndex((p) => p.phase === ph);
      const from = Math.max(0, first - 1);
      const data = points.slice(from).filter((p, i) => i === 0 || p.phase === ph).map((p) => ({ time: p.time as Time, value: unit === "usd" ? p.usd : p.r }));
      return { s, data };
    });
    const markers = starts.slice(1).map((st) => ({ time: st.time as Time, position: "aboveBar" as const, color: css(PHASE_COLOR[st.phase] ?? "--color-cream"), shape: "arrowDown" as const, text: st.label }));
    if (markers.length) createSeriesMarkers(series[0].s, markers);
    let stop: (() => void) | undefined;
    const all = series.flatMap(({ data }) => data);
    if (animated && series.length === 1 && series[0].data.length > 3) {
      // draw in from the left once (Full only)
      const data = series[0].data;
      let i = 2;
      series[0].s.setData(data.slice(0, i));
      chart.timeScale().fitContent();
      const id = setInterval(() => { i = Math.min(data.length, i + 4); series[0].s.setData(data.slice(0, i)); chart.timeScale().fitContent(); if (i >= data.length) clearInterval(id); }, 16);
      stop = () => clearInterval(id);
    } else {
      for (const { s, data } of series) s.setData(data);
      chart.timeScale().fitContent();
    }
    void all;
    chart.subscribeCrosshairMove((p) => {
      if (!p.point || !p.time) return setTip(null);
      const c = points.find((x) => x.time === p.time);
      if (!c) return setTip(null);
      setTip({ x: p.point.x, y: p.point.y, date: c.start ? `${c.date}${c.clock ? ` ${c.clock}` : ""} · start` : `${c.date}${c.clock ? ` ${c.clock}` : ""} · ${c.phase}`, lines: [
        ["Equity", unit === "usd" ? money(c.usd) : rr(c.r), c.usd >= 0 ? "text-pnl-up" : "text-pnl-down"],
        [perTrade ? "Trade" : "Day", unit === "usd" ? money(c.day_usd) : rr(c.day_r), c.day_usd >= 0 ? "text-pnl-up" : "text-pnl-down"],
        ...(perTrade ? [] : [["Trades", String(c.trades)] as [string, string]]),
        ["Drawdown", money(c.dd_usd), c.dd_usd < 0 ? "text-pnl-down" : undefined],
      ] });
    });
    const refit = fitOnResize(el, chart);
    return () => { stop?.(); refit(); chart.remove(); };
  }, [points, starts, unit, animated, perTrade]);
  return (
    <div className="relative h-full w-full">
      <div className="absolute left-3 top-2 z-10 flex flex-wrap items-center gap-3 font-mono text-[11px] text-text-3">
        {[...new Set(points.map((p) => p.phase))].map((ph) => (
          <span key={ph} className="flex items-center gap-1.5"><span className="size-2 rounded-full" style={{ background: `var(${PHASE_COLOR[ph] ?? "--color-cream"})` }} />
            {ph === "development" ? "Development · months 1–6 (backtest)" : ph[0].toUpperCase() + ph.slice(1)}</span>
        ))}
        {points.some((p) => p.phase === "development") && <span className="flex items-center gap-1.5 opacity-70"><span className="size-2 rounded-full border border-text-3" />Walk-forward 7–9: totals only</span>}
      </div>
      <div ref={ref} className="absolute inset-0 top-6" data-equity-chart />
      <Tooltip tip={tip} />
    </div>
  );
}

/** A panel header: what is shown (an account type or an account), its rule in words, the lock date when it locked. */
export function RuleHeader({ title, sub, rule, run, extra }: { title: string; sub?: string; rule: string; run?: Run | null; extra?: ReactNode }) {
  return (
    <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1" data-rule-header={title}>
      <span className="font-mono text-[13px] text-text-1">{title}</span>
      {sub && <span className="text-[12px] text-text-2">{sub}</span>}
      <span className="font-mono text-[11px] text-text-3">{rule}</span>
      {run && run.locked_at !== null && (
        <span className="rounded-full border border-info/40 bg-info/10 px-2 py-0.5 font-mono text-[10.5px] text-info" data-hint="From then on the floor stays put, so the room to it grows with the balance">
          floor locked {fmtDate(run.dates[run.locked_at])} at {money(run.floor[run.locked_at], false)}</span>
      )}
      {run && run.breach_at !== null && <span className="rounded-full border border-crit/50 bg-crit/10 px-2 py-0.5 font-mono text-[10.5px] text-crit">floor reached {fmtDate(run.dates[run.breach_at])}</span>}
      {run && run.closest_at !== null && run.breach_at === null && (
        <span className="font-mono text-[11px] text-text-2" data-closest>closest approach {money(run.room[run.closest_at], false)} from the floor, {fmtDate(run.dates[run.closest_at])}</span>
      )}
      {extra}
    </div>
  );
}

/**
 * Drawdown under one rule (SPEC §6.7): the drawdown from the end-of-day high, and the floor drawn the same way
 * (floor − high) as a stepped dashed line; a breach is the drawdown reaching it; the closest approach marked.
 */
export function DrawdownChart({ run, header, markers = [] }: {
  run: Run; header: ReactNode;
  /** lifecycle events on the chart: a pass (funded starts) or a floor reached (a new evaluation starts) */
  markers?: { date: string; kind: "passed" | "failed"; text: string }[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || !run.dates.length) return;
    const chart = baseChart(el);
    const down = css("--color-pnl-down");
    const dd = run.balance.map((b, i) => b - run.high[i]);
    const lim = run.floor.map((f, i) => f - run.high[i]);
    const minDd = Math.min(...dd);
    const lo = Math.min(minDd, -run.drawdown_usd) * 1.15;
    const s = chart.addSeries(AreaSeries, {
      lineColor: down, lineWidth: 1, topColor: "rgba(255,123,110,.05)", bottomColor: "rgba(255,123,110,.28)", invertFilledArea: true,
      priceFormat: { type: "custom", formatter: (v: number) => money(v) }, priceLineVisible: false,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: lo, maxValue: 0 } }),
    } as never);
    s.setData(run.dates.map((d, i) => ({ time: d as Time, value: dd[i] })));
    const f = chart.addSeries(LineSeries, {
      color: css("--color-crit"), lineWidth: 1, lineStyle: LineStyle.Dashed, lineType: LineType.WithSteps,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: "floor", autoscaleInfoProvider: () => null,
    });
    f.setData(run.dates.map((d, i) => ({ time: d as Time, value: lim[i] })));
    chart.priceScale("right").applyOptions({ scaleMargins: { top: 0, bottom: 0.04 } });
    const ms: SeriesMarker<Time>[] = [];
    // the closest approach: a dot on the chart; its value and date are in the header (text here would clip at the edge)
    if (run.closest_at !== null) ms.push({ time: run.dates[run.closest_at] as Time, position: "belowBar", color: css("--color-cream"), shape: "circle" });
    for (const m of markers) {
      ms.push(m.kind === "passed"
        ? { time: m.date as Time, position: "belowBar", color: css("--color-ok"), shape: "circle", text: m.text }
        : { time: m.date as Time, position: "belowBar", color: css("--color-crit"), shape: "arrowDown", text: m.text });
    }
    if (ms.length) createSeriesMarkers(s, ms);
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((p) => {
      if (!p.point || !p.time) return setTip(null);
      const i = run.dates.indexOf(p.time as string);
      if (i < 0) return setTip(null);
      setTip({ x: p.point.x, y: p.point.y, date: run.dates[i], lines: [
        ["Drawdown", money(dd[i]), dd[i] < 0 ? "text-pnl-down" : undefined],
        ["Balance", money(run.balance[i], false)], ["Floor", money(run.floor[i], false)],
        ["Room to the floor", money(run.room[i], false)],
      ] });
    });
    const refit = fitOnResize(el, chart);
    return () => { refit(); chart.remove(); };
  }, [run, markers]);
  return (
    <div className="flex h-full flex-col" data-drawdown={run.account}>
      {header}
      <div className="relative min-h-0 flex-1"><div ref={ref} className="absolute inset-0" /><Tooltip tip={tip} /></div>
    </div>
  );
}

// the Monte Carlo's draw-out (owner 2026-11-15): the fan, median, backtest-order line and smallest / largest paths draw
// out from the start to the end over DRAW_S on an ease-out; a click on the chart or any key skips to the end; the
// numbers show at once; Calm draws it whole
const DRAW_S = 1.25;
const easeOut = (t: number) => 1 - (1 - t) ** 3;

export type FanData = Pick<McFan, "n" | "bands" | "worst" | "best" | "base" | "base_floor">;

/** The Monte Carlo fan: 1,000 paths of whole days, stopped where their run ends; the x-axis is trading days. */
export function MonteCarloFan({ fan, header, animated, start = "start", target = null, targetLabel = "profit target", passDay = null, medianPassDay = null, note }: {
  fan: FanData; header: ReactNode; animated: boolean;
  /** the x-axis' first label ("start", or "today" for an account from today) */
  start?: string;
  /** the target in the plotted coordinates (an evaluation's profit target), drawn as a line */
  target?: number | null; targetLabel?: string;
  /** the backtest-order path's pass day (a marker on it) and the median day to pass (a tick on the axis) */
  passDay?: number | null; medianPassDay?: number | null;
  note?: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const clip = useRef<SVGRectElement>(null);
  const [done, setDone] = useState(!animated);
  const clipId = useId().replace(/:/g, "");
  const { bands, worst, best, base, base_floor: baseFloor } = fan;
  const W = 1000, H = 300, P = 8;
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
  }, [fan, animated]);
  if (!bands.length) return <>{header}<p className="text-[13px] text-text-2">No backtest days in this selection.</p></>;
  const all = [...bands.map((b) => b.p5), ...bands.map((b) => b.p95), ...worst, ...best, ...baseFloor];
  const lo = Math.min(...all), hi = Math.max(...all);
  const x = (i: number) => P + (i / Math.max(1, bands.length - 1)) * (W - 2 * P);
  const y = (v: number) => P + (1 - (v - lo) / (hi - lo || 1)) * (H - 2 * P);
  const band = (a: "p95" | "p75", b: "p5" | "p25") =>
    `M${bands.map((q, i) => `${x(i)},${y(q[a])}`).join(" L")} L${bands.map((_, i) => `${x(bands.length - 1 - i)},${y(bands[bands.length - 1 - i][b])}`).join(" L")} Z`;
  const line = (arr: number[]) => `M${arr.map((v, i) => `${x(i)},${y(v)}`).join(" L")}`;
  const steps = (arr: number[]) => `M${arr.map((v, i) => (i ? `${x(i)},${y(arr[i - 1])} L${x(i)},${y(v)}` : `${x(0)},${y(v)}`)).join(" L")}`;
  const step = niceStep((hi - lo) / 5);
  const ticks: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);
  // trading days from the start (no calendar months)
  const ticksX: { i: number; label: string }[] = [];
  const stepX = (bands.length - 1) <= 30 ? 5 : (bands.length - 1) <= 60 ? 10 : 20;
  for (let i = 0; i <= bands.length - 1; i += stepX) ticksX.push({ i, label: i ? `day ${i}` : start });
  return (
    <div className="flex h-full flex-col" data-montecarlo data-drawn={done ? "1" : "0"}>
      {header}
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-text-3">
        <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-xs bg-gold/15" />5–95%</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-xs bg-gold/30" />25–75%</span>
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-cream" />median</span>
        <span className="flex items-center gap-1.5"><span className="h-0 w-4 border-t border-dashed border-text-2" />smallest / largest drawdown</span>
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-orb-pika" />backtest order</span>
        <span className="flex items-center gap-1.5"><span className="h-0 w-4 border-t border-dashed border-crit" />its floor</span>
        {target !== null && <span className="flex items-center gap-1.5"><span className="h-0 w-4 border-t border-dashed border-ok" />{targetLabel}</span>}
        {passDay !== null && <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-ok" />passed</span>}
        {medianPassDay !== null && <span className="flex items-center gap-1.5"><span className="h-3 w-0 border-l border-dashed border-ok" />median day to pass</span>}
        <span>{note ?? `${fan.n.toLocaleString()} paths of whole days drawn with replacement; a path stops where its run ends`}</span>
        {!done && <span className="ml-auto text-text-3" data-skip-hint>click or press a key to skip</span>}
      </div>
      <div ref={box} className="relative min-h-0 flex-1 cursor-default pb-5 pr-[86px]">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block size-full" role="img" aria-label="Monte Carlo fan">
          <defs><clipPath id={clipId}><rect ref={clip} x={0} y={0} width={animated ? P : W} height={H} /></clipPath></defs>
          <g clipPath={`url(#${clipId})`}>
            <path d={band("p95", "p5")} fill="rgba(255,224,194,.10)" />
            <path d={band("p75", "p25")} fill="rgba(255,224,194,.18)" />
            <path d={line(bands.map((b) => b.p50))} fill="none" stroke="#ffe0c2" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
            <path d={line(worst)} fill="none" stroke="#b4b4b4" strokeWidth={1} strokeDasharray="5 5" vectorEffect="non-scaling-stroke" />
            <path d={line(best)} fill="none" stroke="#b4b4b4" strokeWidth={1} strokeDasharray="5 5" vectorEffect="non-scaling-stroke" />
            <path d={line(base)} fill="none" stroke="#f2b950" strokeWidth={1.2} vectorEffect="non-scaling-stroke" opacity={0.8} />
            <path d={steps(baseFloor)} fill="none" stroke="#ff4f4f" strokeDasharray="6 5" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          </g>
          {target !== null && target >= lo && target <= hi && <line x1={P} x2={W - P} y1={y(target)} y2={y(target)} stroke="#6fcf97" strokeDasharray="4 6" strokeWidth={1} vectorEffect="non-scaling-stroke" />}
          {medianPassDay !== null && <line x1={x(medianPassDay)} x2={x(medianPassDay)} y1={P} y2={H - P} stroke="#6fcf97" strokeDasharray="3 5" strokeWidth={1} opacity={0.6} vectorEffect="non-scaling-stroke" />}
          <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="#2a2a2a" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        </svg>
        {ticks.map((v) => (
          <span key={v} className="absolute right-0 -translate-y-1/2 font-mono text-[11px] tabular-nums text-text-3" style={{ top: `calc((100% - 20px) * ${y(v) / H})` }}>{money(v, true)}</span>
        ))}
        {ticksX.map((m) => (
          <span key={m.i} className="absolute bottom-0 -translate-x-1/2 font-mono text-[11px] text-text-3" style={{ left: `calc((100% - 86px) * ${x(m.i) / W})` }}>{m.label}</span>
        ))}
        {/* the backtest-order path's pass: a marker on it (HTML, so the stretched SVG never stretches it) */}
        {passDay !== null && passDay < fan.base.length && done && (
          <span className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2" data-pass-marker
            style={{ left: `calc((100% - 86px) * ${x(passDay) / W})`, top: `calc((100% - 20px) * ${y(fan.base[passDay]) / H})` }}>
            <span className="block size-2.5 rounded-full border border-bg-0 bg-ok shadow-[0_0_0_3px_rgba(111,207,151,.25)]" />
            <span className="absolute left-3 top-[-6px] whitespace-nowrap font-mono text-[10.5px] text-ok">passed</span>
          </span>
        )}
      </div>
    </div>
  );
}

/** Live vs expected (SPEC §9b item 6): the demo / live R curve over the band the backtest predicts for as many trades. */
export function LiveVsExpected({ live, band, empty }: { live: number[]; band: Band[]; empty: ReactNode }) {
  if (live.length < 2 || band.length < 2) return <>{empty}</>;
  const W = 1000, H = 300, P = 8;
  const all = [...band.map((b) => b.p5), ...band.map((b) => b.p95), ...live];
  const lo = Math.min(...all, 0), hi = Math.max(...all, 0);
  const x = (i: number) => P + (i / (band.length - 1)) * (W - 2 * P);
  const y = (v: number) => P + (1 - (v - lo) / (hi - lo || 1)) * (H - 2 * P);
  const area = `M${band.map((b, i) => `${x(i)},${y(b.p95)}`).join(" L")} L${band.map((_, i) => `${x(band.length - 1 - i)},${y(band[band.length - 1 - i].p5)}`).join(" L")} Z`;
  const line = (arr: number[]) => `M${arr.map((v, i) => `${x(i)},${y(v)}`).join(" L")}`;
  const last = live[live.length - 1], inside = last >= band[band.length - 1].p5 && last <= band[band.length - 1].p95;
  const step = niceStep((hi - lo) / 5);
  const ticks: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);
  return (
    <div className="flex h-full flex-col" data-live-vs-expected>
      <div className="mb-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="text-[13px] text-text-1">{live.length - 1} live / demo trades against the backtest</span>
        <span className="font-mono text-[11px] text-text-3">band: 1,000 draws of whole development days to the same trade count</span>
        <span className={`ml-auto font-mono text-[12px] ${inside ? "text-ok" : "text-warn"}`}>{rr(last)} · {inside ? "inside" : "outside"} the 5–95% band</span>
      </div>
      <div className="relative min-h-0 flex-1 pb-5 pr-[70px]">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block size-full" role="img" aria-label="Live versus expected">
          <path d={area} fill="rgba(255,224,194,.12)" />
          <path d={line(band.map((b) => b.p50))} fill="none" stroke="#ffe0c2" strokeWidth={1.2} strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
          <path d={line(live)} fill="none" stroke="#79b8ff" strokeWidth={2} vectorEffect="non-scaling-stroke" />
          <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="#2a2a2a" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        </svg>
        {ticks.map((v) => (
          <span key={v} className="absolute right-0 -translate-y-1/2 font-mono text-[11px] tabular-nums text-text-3" style={{ top: `calc((100% - 20px) * ${y(v) / H})` }}>{rr(v)}</span>
        ))}
        <span className="absolute bottom-0 left-0 font-mono text-[11px] text-text-3">trade 0</span>
        <span className="absolute bottom-0 right-[70px] font-mono text-[11px] text-text-3">trade {live.length - 1}</span>
      </div>
    </div>
  );
}
