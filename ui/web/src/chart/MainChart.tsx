// The main chart (shared component; design-system/coverage.md "main chart"; MASTER.md §3.6, §11; SPEC §9b item 3):
// NQ or ES 1-minute candles on TradingView Lightweight Charts with our own overlay (`overlay.ts`), following a replay
// clock. Toolbar: NQ | ES, the layers (zones, chart lines, highs / lows, nodes, touches), Table (the same numbers as a
// table, MASTER §11's accessibility fallback) and full screen (F). The crosshair tooltip shows the bar and, over a level,
// its role timeline for the day so far; over an orb, the node's strike, value and share of the King.
import { useEffect, useMemo, useRef, useState } from "react";
import { CandlestickSeries, ColorType, CrosshairMode, LineStyle, createChart, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp } from "lightweight-charts";
import { Expand, Minimize, Table2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Half, Level, MapName } from "@/pages/review/types";
import { clockOf, gexText, lastAt, roleAt, segAt } from "./data";
import { ReviewOverlay, RIGHT_ROOM_PX, ROLE_COLOR, type Orb, type TradeLines } from "./overlay";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const LAYERS = [
  { id: "zones", label: "Zones" }, { id: "lines", label: "Lines" }, { id: "highs", label: "Highs / lows" },
  { id: "nodes", label: "Nodes" }, { id: "touches", label: "Touches" },
] as const;
export type LayerId = (typeof LAYERS)[number]["id"];
const CHAINS: Record<"NQ" | "ES", MapName[]> = { NQ: ["QQQ", "NDXP"], ES: ["SPY", "SPX"] };
const VISIBLE_BARS = 150;     // bars in view when a card opens (the rest scrolls)

export function orbsAt(half: Half, inst: "NQ" | "ES", clock: number): Orb[] {
  const out: Orb[] = [];
  for (const m of CHAINS[inst]) {
    const snap = lastAt(half.maps[m] ?? [], clock, (s) => s.t);
    if (!snap) continue;
    for (const l of half.levels[inst]) {
      if (!l.node || l.chain !== m || l.strike == null) continue;
      const band = segAt(l, clock);
      if (!band) continue;
      const hit = snap.s.find((x) => Math.abs(x[0] - l.strike!) < 1e-6);
      const v = hit ? hit[1] : 0;
      out.push({ price: (band[0] + band[1]) / 2, value: v, share: snap.max ? Math.abs(v) / snap.max : 0,
        king: snap.king != null && Math.abs(snap.king - l.strike) < 1e-6, label: `${m} ${l.strike}`, chain: m, strike: l.strike });
    }
  }
  return out;
}

/** a level's role timeline so far, in words (§9b item 3: hovering shows the level's role timeline for the day) */
export function roleStory(l: Level, limit: number, off: number): string[] {
  const out: string[] = [];
  let prev: Level["roles"][number] | null = null;
  for (const r of l.roles) {
    if (r[0] > limit) break;
    const at = clockOf(r[0], off);
    if (!prev) out.push(`${r[1] ?? "no role"} from ${at}`);
    else if (r[2] && !prev[2]) out.push(`crossed ${at} → ${r[1]} (provisional)`);
    else if (r[3] && !prev[3]) out.push(`crossed back ${at}`);
    else if (!r[2] && prev[2] && r[4] > prev[4]) out.push(`flipped ${at} → ${r[1]}`);
    else if (!r[2] && prev[2]) out.push(`reclaimed ${at} → ${r[1]}`);
    else if (r[1] !== prev[1]) out.push(`${r[1]} from ${at}`);
    prev = r;
  }
  return out.length ? out : ["no role yet"];
}

type Tip = { x: number; y: number; head: string; lines: [string, string, string?][] } | null;

export function MainChart({ half, inst, off, clock, limit, trade, layers, onLayer, onInst, table, onTable, animated, height = 560,
  focus = true, onFocus, focusUids, ownZone = null }: {
  half: Half; inst: "NQ" | "ES"; off: number; clock: number; limit: number; trade: TradeLines | null;
  layers: Record<LayerId, boolean>; onLayer: (id: LayerId) => void; onInst: (i: "NQ" | "ES") => void;
  table: boolean; onTable: () => void; animated: boolean; height?: number | string;
  /** Focus: the setup's own levels and zone at full strength, the rest faint (NQ only) */
  focus?: boolean; onFocus?: () => void; focusUids?: Set<string>; ownZone?: string | null;
}) {
  const box = useRef<HTMLDivElement>(null);
  const plot = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; series: ISeriesApi<"Candlestick">; overlay: ReviewOverlay } | null>(null);
  const [tip, setTip] = useState<Tip>(null);
  const [full, setFull] = useState(false);
  const bars = half.bars[inst];
  const shown = useMemo(() => bars.filter((b) => b[0] <= clock), [bars, clock]);
  const orbs = useMemo(() => orbsAt(half, inst, clock), [half, inst, clock]);
  const lastShown = useRef<{ inst: string; n: number; first: number } | null>(null);
  void animated;
  // what the tooltip reads (without re-subscribing)
  const stateRef = useRef({ levels: [] as Level[], limit: 0, off });
  stateRef.current = { levels: half.levels[inst], limit, off };


  // the chart, once
  useEffect(() => {
    const el = plot.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--color-text-3"), fontFamily: "Geist Mono Variable, monospace", fontSize: 11, attributionLogo: true },
      grid: { vertLines: { visible: false }, horzLines: { color: css("--color-line-chart") } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffsetPixels: RIGHT_ROOM_PX, shiftVisibleRangeOnNewBar: true },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(255,224,194,.4)", style: LineStyle.Dashed, labelBackgroundColor: css("--color-bg-3") },
        horzLine: { color: "rgba(255,224,194,.4)", style: LineStyle.Dashed, labelBackgroundColor: css("--color-bg-3") },
      },
      localization: { priceFormatter: (p: number) => p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: css("--color-candle-up"), downColor: css("--color-candle-down"), borderUpColor: css("--color-candle-up"),
      borderDownColor: "#9AA8F0", wickUpColor: "rgba(239,230,210,.75)", wickDownColor: "rgba(132,148,230,.85)",
      priceLineVisible: false, lastValueVisible: true,
    });
    const overlay = new ReviewOverlay({ clock: 0, limit: 0, barTimes: [], levels: [], zones: [], touches: [], kings: [], orbs: [], trade: null, layers: {}, hover: null,
      focusUids: new Set(), ownZone: null, focus: false });
    series.attachPrimitive(overlay);
    api.current = { chart, series, overlay };
    (window as unknown as { __cfOverlay?: ReviewOverlay }).__cfOverlay = overlay;   // the stage-4 shoot script reads it
    chart.subscribeCrosshairMove((p) => {
      const a = api.current;
      if (!a || !p.point) { setTip(null); a?.overlay.set({ hover: null }); return; }
      const { x, y } = p.point;
      const orb = a.overlay.orbHits.find((o) => Math.hypot((o.x ?? 0) - x, (o.y ?? 0) - y) < 10);
      if (orb) {
        a.overlay.set({ hover: null });
        setTip({ x, y, head: `${orb.chain} ${orb.strike}${orb.king ? " · King" : ""}`, lines: [
          ["Value", `${gexText(orb.value)} (${orb.value >= 0 ? "Pika" : "Barney"})`, orb.value >= 0 ? "text-orb-pika" : "text-orb-barney"],
          ["Share of the King", `${Math.round(orb.share * 100)}%`],
          ["On the chart", orb.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })]] });
        return;
      }
      const near = a.overlay.hits.filter((h) => Math.abs(h.y - y) < 5 && x >= h.x0 - 4).sort((m, n) => Math.abs(m.y - y) - Math.abs(n.y - y))[0];
      a.overlay.set({ hover: near?.uid ?? null });
      const bar = p.time != null ? (p.seriesData.get(a.series) as { open?: number; high?: number; low?: number; close?: number } | undefined) : undefined;
      const lines: [string, string, string?][] = [];
      if (bar && bar.open != null) lines.push(["O H L C", `${bar.open} ${bar.high} ${bar.low} ${bar.close}`]);
      const lv = near ? stateRef.current.levels.find((l) => l.uid === near.uid) : null;
      if (lv) for (const s of roleStory(lv, stateRef.current.limit, stateRef.current.off)) lines.push(["", s]);
      const t = typeof p.time === "number" ? p.time - stateRef.current.off : null;
      setTip(lines.length ? { x, y, head: lv ? lv.label : t != null ? `${clockOf(t, stateRef.current.off)} ET` : "", lines } : null);
    });
    // the attribution link (kept: its licence) carries a native title; the panel shows hints in its own tooltip instead
    const hintTitles = () => el.querySelectorAll<HTMLElement>("[title]").forEach((n) => { n.setAttribute("data-hint", n.getAttribute("title") ?? ""); n.removeAttribute("title"); });
    hintTitles();
    const mo = new MutationObserver(hintTitles);
    mo.observe(el, { subtree: true, childList: true, attributes: true, attributeFilter: ["title"] });
    const onFs = () => setFull(document.fullscreenElement === box.current);
    document.addEventListener("fullscreenchange", onFs);
    return () => { mo.disconnect(); document.removeEventListener("fullscreenchange", onFs); chart.remove(); api.current = null; };
  }, []);

  // bars: the whole set on an instrument or card change, one bar at a time while the replay steps on
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    const data = shown.map((b) => ({ time: (b[0] + off) as UTCTimestamp, open: b[1], high: b[2], low: b[3], close: b[4] }));
    const prev = lastShown.current;
    const sameRun = prev && prev.inst === inst && prev.first === (shown[0]?.[0] ?? 0) && data.length >= prev.n && data.length - prev.n <= 2 && prev.n > 0;
    if (sameRun) {
      for (let i = Math.max(0, prev!.n - 1); i < data.length; i++) a.series.update(data[i]);
    } else {
      a.series.setData(data);
      const n = data.length;
      if (!prev || prev.inst !== inst || prev.first !== (shown[0]?.[0] ?? 0)) {
        // the last VISIBLE_BARS bars, with RIGHT_ROOM_PX kept right of the last one for the orbs and the labels
        const ts = a.chart.timeScale(), wpx = Math.max(200, ts.width()), room = RIGHT_ROOM_PX / Math.max(1, wpx - RIGHT_ROOM_PX) * VISIBLE_BARS;
        ts.setVisibleLogicalRange({ from: Math.max(0, n - VISIBLE_BARS), to: n - 1 + room });
      }
    }
    lastShown.current = { inst, n: data.length, first: shown[0]?.[0] ?? 0 };
    a.overlay.set({ barTimes: shown.map((b) => b[0]) });
  }, [shown, inst, off]);

  // the trade's exact prices on the price axis (coloured labels; the lines themselves are the overlay's, from the decision on)
  useEffect(() => {
    const a = api.current;
    if (!a || !trade || inst !== "NQ") return;
    const mk = (price: number | null, color: string) => price == null ? null : a.series.createPriceLine({ price, color, lineVisible: false, axisLabelVisible: true, title: "" });
    const lines = [mk(trade.target, "#5FD39B"), mk(trade.stop, "#FF4F4F"), mk(trade.entry, "#FFE0C2")];
    return () => { for (const l of lines) if (l) a.series.removePriceLine(l); };
  }, [trade, inst]);

  // the overlay
  useEffect(() => {
    api.current?.overlay.set({ clock, limit, levels: half.levels[inst], zones: inst === "NQ" ? half.zones : [],
      touches: inst === "NQ" ? half.events : [], kings: CHAINS[inst].flatMap((m) => half.kings[m] ?? []), orbs,
      trade: inst === "NQ" ? trade : null, layers, focus: focus && inst === "NQ", focusUids: focusUids ?? new Set(), ownZone });
  }, [half, inst, clock, limit, orbs, trade, layers, focus, focusUids, ownZone]);

  const toggleFull = () => {
    const el = box.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen(); else void el.requestFullscreen?.();
  };

  const tableRows = useMemo(() => {
    const last = shown[shown.length - 1]?.[4] ?? 0;
    return half.levels[inst].map((l) => ({ l, band: segAt(l, clock), r: roleAt(l.roles, clock) }))
      .filter((x) => x.band).map((x) => ({ ...x, mid: (x.band![0] + x.band![1]) / 2 }))
      .sort((a, b) => b.mid - a.mid).map((x) => ({ ...x, dist: x.mid - last }));
  }, [half, inst, clock, shown]);

  return (
    <div ref={box} id="cf-main-chart" className={cn("relative flex flex-col bg-bg-1", full && "h-screen p-3")} data-chart>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2" role="toolbar" aria-label="Chart">
        <div className="flex rounded-full border border-line-strong p-0.5" role="radiogroup" aria-label="Instrument">
          {(["NQ", "ES"] as const).map((i) => (
            <button key={i} type="button" role="radio" aria-checked={inst === i} onClick={() => onInst(i)}
              className={cn("h-7 rounded-full px-3 font-mono text-[12px]", inst === i ? "bg-caramel text-cream" : "text-text-2 hover:text-text-1")}>{i}</button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1" aria-label="Layers">
          {LAYERS.map((l) => (
            <button key={l.id} type="button" aria-pressed={layers[l.id]} onClick={() => onLayer(l.id)} data-layer={l.id}
              className={cn("h-7 rounded-full border px-2.5 text-[12px]", layers[l.id] ? "border-line-strong bg-bg-3 text-text-1" : "border-transparent text-text-3 hover:text-text-2")}>{l.label}</button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-1">
          {onFocus && (
            <button type="button" onClick={onFocus} aria-pressed={focus} data-focus-toggle
              data-hint={focus ? "Focus on: the setup's zone, its levels and the target at full strength, the rest faint. Click to show all equally" : "Show the setup's zone, its levels and the target at full strength, the rest faint"}
              className={cn("h-7 rounded-full border px-2.5 text-[12px]", focus ? "border-line-strong bg-bg-3 text-text-1" : "border-transparent text-text-2 hover:text-text-1")}>Focus</button>
          )}
          <button type="button" onClick={onTable} aria-pressed={table} data-hint="The same numbers as a table"
            className={cn("inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[12px]", table ? "border-line-strong bg-bg-3 text-text-1" : "border-transparent text-text-2 hover:text-text-1")}>
            <Table2 className="size-3.5" aria-hidden="true" />Table</button>
          <button type="button" onClick={toggleFull} data-hint={full ? "Leave full screen (F)" : "Full screen (F)"} aria-label="Full screen"
            className="inline-flex size-7 items-center justify-center rounded-full text-text-2 hover:bg-bg-3 hover:text-text-1">
            {full ? <Minimize className="size-4" aria-hidden="true" /> : <Expand className="size-4" aria-hidden="true" />}
          </button>
        </div>
      </div>
      <div className="relative" style={{ height: full ? undefined : height, flex: full ? 1 : undefined }}>
        <div ref={plot} className={cn("absolute inset-0", table && "invisible")} data-plot />
        {tip && !table && (
          <div className="pointer-events-none absolute z-20 max-w-[320px] rounded-sm border border-line-strong bg-bg-3 px-3 py-2 shadow-[0_12px_32px_rgba(0,0,0,.55)]"
            style={{ left: Math.min(tip.x + 14, (plot.current?.clientWidth ?? 800) - 330), top: Math.max(8, tip.y - 60) }} data-tip>
            {tip.head && <div className="eyebrow">{tip.head}</div>}
            {tip.lines.map(([k, v, cls], i) => (
              <div key={i} className="mt-1 flex items-baseline justify-between gap-4">
                {k && <span className="text-[12px] text-text-2">{k}</span>}<span className={`font-mono text-[12px] ${cls ?? "text-text-1"}`}>{v}</span>
              </div>
            ))}
          </div>
        )}
        {table && (
          <div className="absolute inset-0 overflow-auto" data-chart-table>
            <table className="w-full border-collapse text-[12px]">
              <thead className="sticky top-0 bg-bg-1 text-left text-text-3">
                <tr>{["Level", "Role", "State", "Band", "From price"].map((h) => <th key={h} className="eyebrow px-3 py-2 font-normal">{h}</th>)}</tr>
              </thead>
              <tbody className="font-mono">
                {tableRows.map(({ l, band, r, dist }) => (
                  <tr key={l.uid} className="border-t border-line">
                    <td className="px-3 py-1.5 text-text-1">{l.label}</td>
                    <td className="px-3 py-1.5" style={{ color: ROLE_COLOR[r?.[1] ?? "none"] }}>{r?.[1] ?? "—"}</td>
                    <td className="px-3 py-1.5 text-text-2">{r ? (r[2] ? (r[3] ? "provisional, crossed back" : "provisional") : r[4] ? `flipped ×${r[4]}` : "original") : "—"}</td>
                    <td className="px-3 py-1.5 text-text-2">{band![0].toFixed(2)} – {band![1].toFixed(2)}</td>
                    <td className="px-3 py-1.5 text-text-2">{dist > 0 ? "+" : dist < 0 ? "−" : ""}{Math.abs(dist).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

export type { Time };
