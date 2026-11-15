// Performance charts (pages/performance.md, MASTER.md §11): equity and drawdown on TradingView Lightweight Charts
// (npm, self-hosted, attribution logo kept), the Monte Carlo fan as SVG. Crosshair tooltips styled to the tokens.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AreaSeries, ColorType, CrosshairMode, LineSeries, LineStyle, LineType, createChart, type IChartApi, type Time } from "lightweight-charts";
import { DATES, MONTHS, MONTH_LABELS, accountRun, monteCarlo, ruleText, type View } from "./data";

function niceStep(raw: number) {
  const p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
import { money, rr } from "@/effects";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** autoSize keeps the bar spacing when the box changes size (a narrower window then shows only the last months):
 *  fit the whole range again on every resize. Returns the cleanup. */
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
    <div className="pointer-events-none absolute z-20 min-w-[150px] rounded-sm border border-line-strong bg-bg-3 px-3 py-2 shadow-[0_12px_32px_rgba(0,0,0,.55)]"
      style={{ left: Math.min(tip.x + 14, 9999), top: Math.max(8, tip.y - 70) }}>
      <div className="eyebrow">{tip.date}</div>
      {tip.lines.map(([k, v, cls]) => (
        <div key={k} className="mt-1 flex items-baseline justify-between gap-4">
          <span className="text-[12px] text-text-2">{k}</span><span className={`font-mono text-[13px] ${cls ?? "text-text-1"}`}>{v}</span>
        </div>
      ))}
    </div>
  );
}

export function EquityChart({ unit, animated, view }: { unit: "usd" | "r"; animated: boolean; view: View }) {
  const CURVE = view.curve;
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip>(null);
  useEffect(() => {
    const el = ref.current!;
    const chart = baseChart(el);
    const gold = css("--color-gold");
    const s = chart.addSeries(AreaSeries, {
      lineColor: gold, lineWidth: 2, topColor: "rgba(255,224,194,.16)", bottomColor: "rgba(255,224,194,0)",
      priceFormat: unit === "usd" ? { type: "custom", formatter: (v: number) => money(v, false) } : { type: "custom", formatter: (v: number) => rr(v) },
      lastValueVisible: true, priceLineVisible: false,
    });
    const data = CURVE.map((c) => ({ time: c.date as Time, value: unit === "usd" ? c.usd : c.r }));
    // draw in from the left once (Full only; Calm shows the whole curve at once)
    let stop: (() => void) | undefined;
    if (animated) {
      let i = 2;
      s.setData(data.slice(0, i));
      chart.timeScale().fitContent();
      const id = setInterval(() => { i = Math.min(data.length, i + 4); s.setData(data.slice(0, i)); chart.timeScale().fitContent(); if (i >= data.length) clearInterval(id); }, 16);
      stop = () => clearInterval(id);
    } else {
      s.setData(data);
      chart.timeScale().fitContent();
    }
    chart.subscribeCrosshairMove((p) => {
      if (!p.point || !p.time) return setTip(null);
      const c = CURVE.find((x) => x.date === p.time);
      if (!c) return setTip(null);
      setTip({ x: p.point.x, y: p.point.y, date: c.date, lines: [["Equity", unit === "usd" ? money(c.usd) : rr(c.r), c.usd >= 0 ? "text-pnl-up" : "text-pnl-down"], ["Drawdown", money(c.dd), c.dd < 0 ? "text-pnl-down" : undefined]] });
    });
    const refit = fitOnResize(el, chart);
    return () => { stop?.(); refit(); chart.remove(); };
  }, [unit, animated, CURVE]);
  return (
    <div className="relative h-full w-full">
      <div className="absolute left-3 top-2 z-10 flex items-center gap-3 font-mono text-[11px] text-text-3">
        <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-gold" />Development · months 1–6 (backtest)</span>
        <span className="flex items-center gap-1.5 opacity-60"><span className="size-2 rounded-full border border-text-3" />Walk-forward 7–9: totals only</span>
      </div>
      <div ref={ref} className="absolute inset-0 top-6" />
      <Tooltip tip={tip} />
    </div>
  );
}

const fmtDate = (d: string) => new Date(d + "T12:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

/** One account's panel header: its preset, phase and drawdown rule in words, and the lock date when it locked. */
export function AccountRuleHeader({ id, extra }: { id: string; extra?: ReactNode }) {
  const run = accountRun(id), a = run.account;
  return (
    <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1" data-account-header={a.id}>
      <span className="font-mono text-[13px] text-text-1">{a.id}</span>
      <span className="text-[12px] text-text-2">{a.preset} · {a.phase}</span>
      <span className="font-mono text-[11px] text-text-3">{ruleText(a.rule)}</span>
      {run.lockedAt !== null && <span className="rounded-full border border-info/40 bg-info/10 px-2 py-0.5 font-mono text-[10.5px] text-info" title="From then on the floor stays put, so the room to it grows with the balance">floor locked {fmtDate(run.dates[run.lockedAt])} at {money(run.floor[run.lockedAt], false)}</span>}
      {extra}
    </div>
  );
}

/**
 * Drawdown of one account under its own rule (SPEC §6.7): the drawdown from its end-of-day high, and its floor drawn
 * the same way (floor − high), a stepped dashed line: a breach is the drawdown reaching it. Trailing with no lock: the
 * line stays at −drawdown; static, or once a trailing floor locks, it falls away as the high rises (the room grows).
 */
export function DrawdownChart({ id }: { id: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip>(null);
  useEffect(() => {
    const run = accountRun(id), rule = run.account.rule;
    const chart = baseChart(ref.current!);
    const down = css("--color-pnl-down");
    const dd = run.balance.map((b, i) => b - run.high[i]);
    const lim = run.floor.map((f, i) => f - run.high[i]);
    const minDd = Math.min(...dd);
    // the visible range: the deepest drawdown and the trailing distance, with room; a locked or static floor that
    // falls further away leaves the chart at the bottom
    const lo = Math.min(minDd, -rule.drawdownUsd) * 1.15;
    const s = chart.addSeries(AreaSeries, {
      lineColor: down, lineWidth: 1, topColor: "rgba(255,123,110,.05)", bottomColor: "rgba(255,123,110,.28)", invertFilledArea: true,
      priceFormat: { type: "custom", formatter: (v: number) => money(v) }, priceLineVisible: false,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: lo, maxValue: 0 } }),
    } as never);
    s.setData(run.dates.map((d, i) => ({ time: d as Time, value: dd[i] })));
    const f = chart.addSeries(LineSeries, {
      color: css("--color-crit"), lineWidth: 1, lineStyle: LineStyle.Dashed, lineType: LineType.WithSteps,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: "floor",
      autoscaleInfoProvider: () => null,
    });
    f.setData(run.dates.map((d, i) => ({ time: d as Time, value: lim[i] })));
    // $0 at the top edge: nothing above it is shaded
    chart.priceScale("right").applyOptions({ scaleMargins: { top: 0, bottom: 0.04 } });
    const wi = dd.indexOf(minDd);
    s.createPriceLine({ price: minDd, color: "rgba(255,224,194,.5)", lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: true, title: `deepest ${fmtDate(run.dates[wi])}` });
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((p) => {
      if (!p.point || !p.time) return setTip(null);
      const i = run.dates.indexOf(p.time as string);
      if (i < 0) return setTip(null);
      setTip({ x: p.point.x, y: p.point.y, date: run.dates[i], lines: [
        ["Drawdown", money(dd[i]), dd[i] < 0 ? "text-pnl-down" : undefined],
        ["Balance", money(run.balance[i], false)], ["Floor", money(run.floor[i], false)],
        ["Room to the floor", money(run.balance[i] - run.floor[i], false)],
      ] });
    });
    const refit = fitOnResize(ref.current!, chart);
    return () => { refit(); chart.remove(); };
  }, [id]);
  return (
    <div className="flex h-full flex-col">
      <AccountRuleHeader id={id} />
      <div className="relative min-h-0 flex-1"><div ref={ref} className="absolute inset-0" /><Tooltip tip={tip} /></div>
    </div>
  );
}

/** Monte Carlo of one account: its whole days reshuffled 1,000 times, each path run through its own drawdown rule. */
export function MonteCarloFan({ id }: { id: string }) {
  const { bands, worst, best, base, baseFloor, pBreach, medianBreachDay, n, account } = monteCarlo(id);
  const W = 1000, H = 300, P = 8;
  const all = [...bands.map((b) => b.p5), ...bands.map((b) => b.p95), ...worst, ...best, ...baseFloor];
  const lo = Math.min(...all), hi = Math.max(...all);
  const x = (i: number) => P + (i / (bands.length - 1)) * (W - 2 * P);
  const y = (v: number) => P + (1 - (v - lo) / (hi - lo)) * (H - 2 * P);
  const band = (a: keyof (typeof bands)[number], b: keyof (typeof bands)[number]) =>
    `M${bands.map((q, i) => `${x(i)},${y(q[a])}`).join(" L")} L${bands.map((q, i) => `${x(bands.length - 1 - i)},${y(bands[bands.length - 1 - i][b])}`).join(" L")} Z`;
  const line = (arr: number[]) => `M${arr.map((v, i) => `${x(i)},${y(v)}`).join(" L")}`;
  // the floor of the backtest order, stepped (it moves at each session end)
  const steps = (arr: number[]) => `M${arr.map((v, i) => (i ? `${x(i)},${y(arr[i - 1])} L${x(i)},${y(v)}` : `${x(0)},${y(v)}`)).join(" L")}`;
  const step = niceStep((hi - lo) / 5);
  const ticks: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);
  // day index where each month starts (index 0 = before the first day)
  const monthStart = MONTHS.map((m) => Math.max(0, DATES.findIndex((d) => d.startsWith(m))));
  const pct = (pBreach * 100).toFixed(1);
  return (
    <div className="flex h-full flex-col">
      <AccountRuleHeader id={id} extra={
        <span className="ml-auto text-[12px] text-text-2">P(reaching the floor) <span className="font-mono text-text-1">{pct}%</span>
          {medianBreachDay !== null && <span className="text-text-3"> · median on trading day {medianBreachDay}</span>}</span>} />
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-text-3">
        <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-xs bg-gold/15" />5–95%</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded-xs bg-gold/30" />25–75%</span>
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-cream" />median</span>
        <span className="flex items-center gap-1.5"><span className="h-0 w-4 border-t border-dashed border-text-2" />smallest / largest drawdown</span>
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-orb-pika" />backtest order</span>
        <span className="flex items-center gap-1.5"><span className="h-0 w-4 border-t border-dashed border-crit" />its floor</span>
        <span className="text-text-3">{n.toLocaleString()} reshuffles by whole days, each through {account.id}&apos;s rule</span>
      </div>
      <div className="relative min-h-0 flex-1 pr-[86px] pb-5">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block size-full" role="img" aria-label={`Monte Carlo fan for ${account.id}; probability of reaching the floor ${pct}%`}>
        <path d={band("p95", "p5")} fill="rgba(255,224,194,.10)" />
        <path d={band("p75", "p25")} fill="rgba(255,224,194,.18)" />
        <path d={line(bands.map((b) => b.p50))} fill="none" stroke="#ffe0c2" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
        <path d={line(worst)} fill="none" stroke="#b4b4b4" strokeWidth={1} strokeDasharray="5 5" vectorEffect="non-scaling-stroke" />
        <path d={line(best)} fill="none" stroke="#b4b4b4" strokeWidth={1} strokeDasharray="5 5" vectorEffect="non-scaling-stroke" />
        <path d={line(base)} fill="none" stroke="#f2b950" strokeWidth={1.2} vectorEffect="non-scaling-stroke" opacity={0.8} />
        <path d={steps(baseFloor)} fill="none" stroke="#ff4f4f" strokeDasharray="6 5" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="#2a2a2a" strokeWidth={1} vectorEffect="non-scaling-stroke" />
      </svg>
      {/* axis labels as HTML, so the stretched SVG never stretches the text */}
      {ticks.map((v) => (
        <span key={v} className="absolute right-0 -translate-y-1/2 font-mono text-[11px] tabular-nums text-text-3" style={{ top: `calc((100% - 20px) * ${y(v) / H})` }}>{money(v, true)}</span>
      ))}
      {MONTH_LABELS.map((m, i) => (
        <span key={m} className="absolute bottom-0 -translate-x-1/2 font-mono text-[11px] text-text-3" style={{ left: `calc((100% - 86px) * ${x(monthStart[i]) / W})` }}>{m}</span>
      ))}
      </div>
    </div>
  );
}
