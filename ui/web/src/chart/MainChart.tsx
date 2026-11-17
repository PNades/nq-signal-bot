// The main chart (shared component; design-system/coverage.md "main chart"; MASTER.md §3.6, §11; SPEC §9b item 3):
// NQ or ES 1-minute candles on TradingView Lightweight Charts with our own overlay (`overlay.ts`), following a replay
// clock. Toolbar: NQ | ES, the layers (zones, chart lines, highs / lows, nodes, touches), one Focus | Table switch
// (Focus: the chart with the setup's levels at full strength and the rest faint; Table: the same numbers as a table,
// MASTER §11's accessibility fallback) and full screen (F). Owner 2026-11-16 (stage-4 review):
// - each symbol fits its own candles: a switch resets the price scale (a stretched ES axis never blanks NQ);
// - hovering a level, a high / low, a node's orb or a touch shows its facts and price, all of them under the pointer in
//   one card sorted by price (no zones: the setup's zone is in the panel on the right); nothing right of the last bar
//   (where the owner reads candle tops on the crosshair); no OHLC box; the card never covers the price axis;
// - the axis carries only the last price and the trade's entry, stop and target tags.
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CandlestickSeries, ColorType, CrosshairMode, LineStyle, createChart, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp } from "lightweight-charts";
import { Expand, Minimize } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Half, Level, MapName } from "@/pages/review/types";
import { clockOf, roleAt, segAt } from "./data";
import { ReviewOverlay, RIGHT_ROOM_PX, ROLE_COLOR, type Hit, type OrbSeries, type TradeLines } from "./overlay";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const LAYERS = [
  { id: "zones", label: "Zones" }, { id: "lines", label: "Lines" }, { id: "highs", label: "Highs / lows" },
  { id: "nodes", label: "Nodes" }, { id: "touches", label: "Touches" },
] as const;
export type LayerId = (typeof LAYERS)[number]["id"];
export type ChartView = "focus" | "table";
const CHAINS: Record<"NQ" | "ES", MapName[]> = { NQ: ["QQQ", "NDXP"], ES: ["SPY", "SPX"] };
const VISIBLE_BARS = 150;     // bars in view when a card opens (the rest scrolls back to 18:00)
const CARD_W = 300;           // the hover card's width

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

type Tip = { x: number; y: number; hits: Hit[] } | null;

export function MainChart({ half, inst, off, clock, limit, trade, layers, onLayer, onInst, view, onView, focus = true, focusUids,
  ownZone = null, replay, minHeight = 360 }: {
  half: Half; inst: "NQ" | "ES"; off: number; clock: number; limit: number; trade: TradeLines | null;
  layers: Record<LayerId, boolean>; onLayer: (id: LayerId) => void; onInst: (i: "NQ" | "ES") => void;
  /** the one switch: the chart (Focus) or the table; exactly one is on */
  view: ChartView; onView: (v: ChartView) => void;
  /** with Focus, the setup's own levels and zone at full strength, the rest faint (NQ only) */
  focus?: boolean; focusUids?: Set<string>; ownZone?: string | null;
  /** the replay bar, under the plot */
  replay?: ReactNode; minHeight?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const plot = useRef<HTMLDivElement>(null);
  const api = useRef<{ chart: IChartApi; series: ISeriesApi<"Candlestick">; overlay: ReviewOverlay } | null>(null);
  const [tip, setTip] = useState<Tip>(null);
  const [full, setFull] = useState(false);
  const table = view === "table";
  const bars = half.bars[inst];
  const shown = useMemo(() => bars.filter((b) => b[0] <= clock), [bars, clock]);
  const orbs: OrbSeries[] = useMemo(() => CHAINS[inst].map((m) => ({ chain: m, snaps: half.maps[m] ?? [] })), [half, inst]);
  const kings = useMemo(() => CHAINS[inst].flatMap((m) => (half.kings[m] ?? []).map((k) => [k[0], k[1], k[2], m] as [number, number, number, string])), [half, inst]);
  const lastShown = useRef<{ inst: string; n: number; first: number } | null>(null);
  // what the pointer reads (without re-subscribing)
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
    const onMove = (p: Parameters<Parameters<IChartApi["subscribeCrosshairMove"]>[0]>[0]) => {
      const a = api.current;
      if (!a) return;
      const right = a.overlay.lastBarRight();
      // nothing in the empty room right of the last bar (the owner reads candle tops there on the crosshair)
      if (!p.point || (right != null && p.point.x > right)) { setTip(null); a.overlay.set({ hover: null }); return; }
      const hits = a.overlay.hitsAt(p.point.x, p.point.y);
      const lv = hits.find((h) => h.kind === "level");
      a.overlay.set({ hover: lv ? lv.key.slice(2) : null });
      setTip(hits.length ? { x: p.point.x, y: p.point.y, hits } : null);
    };
    chart.subscribeCrosshairMove(onMove);
    // the attribution link (kept: its licence) carries a native title; the panel shows hints in its own tooltip instead
    const hintTitles = () => el.querySelectorAll<HTMLElement>("[title]").forEach((n) => { n.setAttribute("data-hint", n.getAttribute("title") ?? ""); n.removeAttribute("title"); });
    hintTitles();
    const mo = new MutationObserver(hintTitles);
    mo.observe(el, { subtree: true, childList: true, attributes: true, attributeFilter: ["title"] });
    const onFs = () => setFull(document.fullscreenElement === box.current);
    document.addEventListener("fullscreenchange", onFs);
    // let go of the chart before removing it: its last crosshair event must not reach a removed chart
    return () => { api.current = null; chart.unsubscribeCrosshairMove(onMove); mo.disconnect(); document.removeEventListener("fullscreenchange", onFs); chart.remove(); };
  }, []);

  // bars: the whole set on an instrument or card change, one bar at a time while the replay steps on
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    const data = shown.map((b) => ({ time: (b[0] + off) as UTCTimestamp, open: b[1], high: b[2], low: b[3], close: b[4] }));
    const prev = lastShown.current;
    const switched = !prev || prev.inst !== inst || prev.first !== (shown[0]?.[0] ?? 0);
    const sameRun = !switched && data.length >= prev!.n && data.length - prev!.n <= 2 && prev!.n > 0;
    if (sameRun) {
      for (let i = Math.max(0, prev!.n - 1); i < data.length; i++) a.series.update(data[i]);
    } else {
      // each symbol fits its own candles: a price axis stretched or squashed on the other symbol (which turns autoscale
      // off) is reset, so a switch never leaves the chart blank (owner 2026-11-16)
      if (switched) a.series.priceScale().applyOptions({ autoScale: true });
      a.series.setData(data);
      const n = data.length;
      if (switched) {
        const ts = a.chart.timeScale(), wpx = Math.max(200, ts.width()), room = RIGHT_ROOM_PX / Math.max(1, wpx - RIGHT_ROOM_PX) * VISIBLE_BARS;
        ts.setVisibleLogicalRange({ from: Math.max(0, n - VISIBLE_BARS), to: n - 1 + room });
      }
    }
    lastShown.current = { inst, n: data.length, first: shown[0]?.[0] ?? 0 };
    a.overlay.set({ barTimes: shown.map((b) => b[0]) });
    setTip(null);
  }, [shown, inst, off]);

  // the axis tags: the trade's entry, stop and target (the lines themselves are the overlay's, from the decision on)
  useEffect(() => {
    const a = api.current;
    if (!a || !trade || inst !== "NQ") return;
    const mk = (price: number | null, color: string, title: string) => price == null ? null : a.series.createPriceLine({ price, color, lineVisible: false, axisLabelVisible: true, title });
    const lines = [mk(trade.target, "#5FD39B", "target"), mk(trade.stop, "#FF4F4F", "stop"), mk(trade.entry, "#FFE0C2", "entry")];
    // on unmount the chart is removed first (the effect above): its series is gone with it, nothing to remove
    return () => { if (api.current !== a) return; for (const l of lines) if (l) a.series.removePriceLine(l); };
  }, [trade, inst]);

  // the overlay
  useEffect(() => {
    api.current?.overlay.set({ clock, limit, levels: half.levels[inst], zones: inst === "NQ" ? half.zones : [],
      touches: inst === "NQ" ? half.events : [], kings, orbs,
      trade: inst === "NQ" ? trade : null, layers, focus: focus && inst === "NQ", focusUids: focusUids ?? new Set(), ownZone });
  }, [half, inst, clock, limit, orbs, kings, trade, layers, focus, focusUids, ownZone]);

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

  // the hover card: beside the pointer, never over the price axis
  const paneW = (plot.current?.clientWidth ?? 800) - (api.current?.chart.priceScale("right").width() ?? 70);
  const tipLeft = tip ? (tip.x + 14 + CARD_W <= paneW - 4 ? tip.x + 14 : Math.max(4, tip.x - 14 - CARD_W)) : 0;

  return (
    <div ref={box} id="cf-main-chart" className={cn("relative flex h-full flex-col bg-bg-1", full && "h-screen p-3")} data-chart>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2" role="toolbar" aria-label="Chart">
        <div className="flex rounded-full border border-line-strong p-0.5" role="radiogroup" aria-label="Instrument">
          {(["NQ", "ES"] as const).map((i) => (
            <button key={i} type="button" role="radio" aria-checked={inst === i} onClick={() => onInst(i)} data-inst={i}
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
          <div className="flex rounded-full border border-line-strong p-0.5" role="radiogroup" aria-label="Chart or table" data-view-switch>
            {([["focus", "Focus", "The chart: the setup's zone, its levels and the target at full strength, the rest faint (A: every level at full strength)"],
               ["table", "Table", "The same numbers as a table"]] as const).map(([v, label, hint]) => (
              <button key={v} type="button" role="radio" aria-checked={view === v} onClick={() => onView(v)} data-view={v} data-hint={hint}
                className={cn("h-7 rounded-full px-3 text-[12px]", view === v ? "bg-caramel text-cream" : "text-text-2 hover:text-text-1")}>{label}</button>
            ))}
          </div>
          <button type="button" onClick={toggleFull} data-hint={full ? "Leave full screen (F)" : "Full screen (F)"} aria-label="Full screen"
            className="inline-flex size-7 items-center justify-center rounded-full text-text-2 hover:bg-bg-3 hover:text-text-1">
            {full ? <Minimize className="size-4" aria-hidden="true" /> : <Expand className="size-4" aria-hidden="true" />}
          </button>
        </div>
      </div>
      <div className="relative min-h-0 flex-1" style={{ minHeight: full ? undefined : minHeight }} onPointerLeave={() => setTip(null)}>
        <div ref={plot} className={cn("absolute inset-0", table && "invisible")} data-plot />
        {tip && !table && (
          <div className="pointer-events-none absolute z-20 rounded-sm border border-line-strong bg-bg-3 px-3 py-2 shadow-[0_12px_32px_rgba(0,0,0,.55)]"
            style={{ left: tipLeft, top: Math.max(6, tip.y - 18), width: CARD_W }} data-tip>
            {tip.hits.slice(0, 8).map((h, i) => {
              const lv = h.kind === "level" ? stateRef.current.levels.find((l) => `l:${l.uid}` === h.key) : null;
              const story = lv ? roleStory(lv, stateRef.current.limit, stateRef.current.off) : [];
              const extra = story.length === 1 && story[0] === "no role yet" ? [] : story;   // the state line already says so
              return (
                <div key={h.key} className={cn("text-[12px]", i > 0 && "mt-2 border-t border-line pt-2")} data-tip-item={h.kind}>
                  <div className="flex items-center gap-1.5"><span className="size-2 shrink-0 rounded-full" style={{ background: h.color }} aria-hidden="true" />
                    <span className="truncate font-medium text-text-1">{h.title}</span></div>
                  <div className="mt-0.5 font-mono text-text-1">{h.lines[0]}</div>
                  {[...h.lines.slice(1), ...extra].map((t, k) => <div key={k} className="text-text-2">{t}</div>)}
                </div>
              );
            })}
            {tip.hits.length > 8 && <div className="mt-2 text-[11px] text-text-3">+{tip.hits.length - 8} more here</div>}
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
      {/* the replay bar under the chart (owner 2026-11-16, round 3: back at the bottom; the dock may cover it) */}
      {replay}
    </div>
  );
}

export type { Time };
