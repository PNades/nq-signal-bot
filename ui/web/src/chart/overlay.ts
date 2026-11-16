// The main chart's own drawing, as one Lightweight Charts series primitive (MASTER.md §3.6, §11; SPEC §9b item 3):
// - zones (merged levels) as shaded bands between their edges, hatched while provisional, coloured by the role;
// - single levels as thin lines, solid in the original role, dashed while provisional, a marker at each cross (◇),
//   flip (◆) and reclaim (○), a label at the right edge;
// - touches with their outcome once known (● held, ✕ broke, ◌ timeout), the card's own touch as a cream ◆;
// - the confirmed King's switches as crowns; the relevant nodes at the replay clock as glowing orbs (Pika gold +,
//   Barney orchid −), the King the brightest with its extra ring;
// - the proposed trade (entry, stop, target from the decision on) and, once revealed, the fill and the exit.
// Everything is clipped at `limit` (the decision time until the owner answers): the chart never draws ahead.
import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type { IChartApi, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, Logical, PrimitivePaneViewZOrder, SeriesAttachedParameter, Time } from "lightweight-charts";
import type { Level, Touch, Zone } from "@/pages/review/types";
import { roleAt } from "./data";

export interface Orb { x?: number; y?: number; price: number; value: number; share: number; king: boolean; label: string; chain: string; strike: number }
export interface TradeLines { from: number; entry: number | null; stop: number; target: number; dir: number; fill?: { t: number; px: number } | null; exit?: { t: number; px: number; result: string } | null }
export interface OverlayState {
  clock: number;      // the replay minute (UTC s, the bar's start)
  limit: number;      // nothing drawn after this (UTC s)
  barTimes: number[]; // the bars on the chart (UTC s), sorted
  levels: Level[];
  zones: Zone[];
  touches: Touch[];
  kings: [number, number, number][];
  orbs: Orb[];
  trade: TradeLines | null;
  layers: Record<string, boolean>;
  hover: string | null;   // a level uid under the pointer (drawn brighter)
  /** the setup's own levels and zone (drawn at full strength); with `focus` on, every other one is faint context */
  focusUids: Set<string>;
  ownZone: string | null;
  focus: boolean;
}

export const ROLE_COLOR = { ceiling: "rgba(255,123,110,.78)", floor: "rgba(95,211,155,.78)", none: "rgba(179,173,163,.6)" };
const ZONE_FILL = { ceiling: "rgba(255,123,110,.10)", floor: "rgba(95,211,155,.10)", none: "rgba(179,173,163,.08)" };
const CONTEXT_ALPHA = 0.28;   // levels outside the setup, with Focus on
const CONTEXT_ZONE_ALPHA = 0.14; // zones outside the setup (they stack, so quieter than the lines)
const PIKA = "#F2B950", BARNEY = "#DD9BE0", KING_RING = "rgba(255,241,220,.7)", CREAM = "#FFE0C2";
const MARK = { held: "#5FD39B", broke: "#FF4F4F", timeout: "#B4B4B4" };
const FONT = '11px "Geist Mono Variable", ui-monospace, monospace';
export const ORB_GAP = 16;    // px right of the clock's bar: the orbs' column
export const LABEL_GAP = 40;  // px right of the clock's bar: the labels' column (levels and the trade), after the orbs
export const RIGHT_ROOM_PX = 190; // the room the chart keeps right of the last bar for both

function bisectRight(a: number[], t: number) {
  let lo = 0, hi = a.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= t) lo = m + 1; else hi = m; }
  return lo;
}

export class ReviewOverlay implements ISeriesPrimitive<Time> {
  s: OverlayState;
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private request: (() => void) | null = null;
  private hatch = new Map<string, CanvasPattern | null>();
  /** where each drawn level line sits at the clock (for the pointer's hit test) */
  hits: { uid: string; y: number; x0: number; x1: number }[] = [];
  orbHits: Orb[] = [];
  /** the level labels of the last drawing (drawn with the trade's in the top layer) */
  private labels: { y: number; text: string; color: string; pri: number }[] = [];

  constructor(s: OverlayState) { this.s = s; }
  attached(p: SeriesAttachedParameter<Time>) { this.chart = p.chart as IChartApi; this.series = p.series as ISeriesApi<"Candlestick">; this.request = p.requestUpdate; }
  detached() { this.chart = null; this.series = null; this.request = null; }
  set(s: Partial<OverlayState>) { this.s = { ...this.s, ...s }; this.request?.(); }
  updateAllViews() {}

  /** the x of a UTC second: a bar spans its minute from its left edge to its right edge */
  xOf(t: number): number | null {
    const bt = this.s.barTimes, ts = this.chart?.timeScale();
    if (!ts || !bt.length) return null;
    const i = bisectRight(bt, t) - 1;
    let logical: number;
    if (i < 0) logical = -0.5 + (t - bt[0]) / 60;
    else if (i === bt.length - 1) logical = i - 0.5 + (t - bt[i]) / 60;      // after the last bar: one bar a minute
    else logical = i - 0.5 + Math.min(1, (t - bt[i]) / 60);                  // in bar i, or the gap after it (its right edge)
    // logicalToCoordinate answers whole bars only (a fractional index reads 0): a whole bar + a share of the spacing
    const base = Math.floor(logical);
    const x0 = ts.logicalToCoordinate(base as Logical), x1 = ts.logicalToCoordinate((base + 1) as Logical);
    if (x0 == null || x1 == null) return null;
    return x0 + (logical - base) * (x1 - x0);
  }
  yOf(p: number): number | null { return this.series?.priceToCoordinate(p) ?? null; }

  paneViews(): readonly IPrimitivePaneView[] {
    const view = (z: PrimitivePaneViewZOrder, draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void): IPrimitivePaneView => ({
      zOrder: () => z,
      renderer: (): IPrimitivePaneRenderer => ({
        draw: (target: CanvasRenderingTarget2D) => target.useMediaCoordinateSpace(({ context, mediaSize }) => draw(context, mediaSize.width, mediaSize.height)),
      }),
    });
    return [view("bottom", (c, w, h) => this.drawZones(c, w, h)), view("normal", (c, w, h) => this.drawLevels(c, w, h)), view("top", (c, w, h) => this.drawTop(c, w, h))];
  }

  private pattern(ctx: CanvasRenderingContext2D, color: string): CanvasPattern | null {
    if (this.hatch.has(color)) return this.hatch.get(color)!;
    const c = document.createElement("canvas");
    c.width = c.height = 7;
    const g = c.getContext("2d")!;
    g.strokeStyle = color; g.lineWidth = 1;
    g.beginPath(); g.moveTo(-1, 8); g.lineTo(8, -1); g.stroke();
    const p = ctx.createPattern(c, "repeat");
    this.hatch.set(color, p);
    return p;
  }

  private end() { return Math.min(this.s.limit, this.s.clock + 60); }

  private drawZones(ctx: CanvasRenderingContext2D, w: number, _h: number) {
    if (!this.s.layers.zones) return;
    const end = this.end();
    for (const z of this.s.zones) {
      const pts = z.pts;
      for (let i = 0; i < pts.length; i++) {
        const p = pts[i];
        if (p[0] > end || p[1] == null || p[2] == null) continue;
        if (p[3] < 2 && !z.own) continue;            // a single level is a line, not a band (owner 2026-11-14)
        const t1 = Math.min(end, i + 1 < pts.length ? pts[i + 1][0] : z.end ?? end);
        const x0 = this.xOf(p[0]), x1 = this.xOf(t1), y0 = this.yOf(p[2]), y1 = this.yOf(p[1]);
        if (x0 == null || x1 == null || y0 == null || y1 == null || x1 < 0 || x0 > w) continue;
        const role = p[4] ?? "none";
        ctx.globalAlpha = this.s.focus && !z.own ? CONTEXT_ZONE_ALPHA : 1;
        ctx.fillStyle = ZONE_FILL[role];
        ctx.fillRect(x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0));
        if (p[5]) {
          const pat = this.pattern(ctx, role === "ceiling" ? "rgba(255,123,110,.35)" : role === "floor" ? "rgba(95,211,155,.35)" : "rgba(179,173,163,.3)");
          if (pat) { ctx.fillStyle = pat; ctx.fillRect(x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0)); }
        }
        if (z.own) {
          ctx.strokeStyle = "rgba(255,224,194,.55)"; ctx.lineWidth = 1;
          ctx.strokeRect(x0 + 0.5, y0 + 0.5, Math.max(1, x1 - x0) - 1, Math.max(1, y1 - y0) - 1);
        }
        ctx.globalAlpha = 1;
      }
    }
  }

  private drawLevels(ctx: CanvasRenderingContext2D, w: number, _h: number) {
    const end = this.end(), L = this.s.layers;
    this.hits = [];
    const labels: { y: number; text: string; color: string }[] = [];
    for (const l of this.s.levels) {
      const layer = l.node ? "nodes" : l.group === "highs/lows" ? "highs" : "lines";
      if (!L[layer]) continue;
      const hot = this.s.hover === l.uid;
      const faint = this.s.focus && !hot && !this.s.focusUids.has(l.uid);
      let lastY: number | null = null, lastColor = "";
      for (const s of l.segs) {
        if (s[0] >= end) break;
        const t1 = Math.min(end, s[1]);
        // split at role changes inside the segment
        const cuts = [s[0], ...l.roles.map((r) => r[0]).filter((t) => t > s[0] && t < t1), t1];
        for (let k = 0; k + 1 < cuts.length; k++) {
          const a = cuts[k], b = cuts[k + 1];
          const x0 = this.xOf(a), x1 = this.xOf(b), y = this.yOf((s[2] + s[3]) / 2);
          if (x0 == null || x1 == null || y == null || x1 < 0 || x0 > w) continue;
          const r = roleAt(l.roles, a);
          const color = ROLE_COLOR[r?.[1] ?? "none"];
          ctx.strokeStyle = color; ctx.lineWidth = hot ? 2 : l.node ? 1 : 1;
          ctx.setLineDash(r?.[2] ? [5, 4] : []);
          ctx.globalAlpha = (l.node ? 0.7 : 1) * (faint ? CONTEXT_ALPHA : 1);
          ctx.beginPath(); ctx.moveTo(x0, Math.round(y) + 0.5); ctx.lineTo(x1, Math.round(y) + 0.5); ctx.stroke();
          ctx.globalAlpha = 1;
          lastY = y; lastColor = color;
        }
      }
      ctx.setLineDash([]);
      // cross / flip / reclaim markers on the line
      for (const [t, name] of l.marks) {
        if (t > end) break;
        const seg = l.segs.find((s) => s[0] <= t && t < s[1]);
        const x = this.xOf(t), y = seg ? this.yOf((seg[2] + seg[3]) / 2) : null;
        if (x == null || y == null) continue;
        const r = roleAt(l.roles, t + 0.001);
        ctx.globalAlpha = faint ? CONTEXT_ALPHA : 1;
        ctx.strokeStyle = ctx.fillStyle = ROLE_COLOR[r?.[1] ?? "none"]; ctx.lineWidth = 1.25;
        ctx.beginPath();
        if (name === "reclaim") { ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.stroke(); }
        else { ctx.moveTo(x, y - 4.5); ctx.lineTo(x + 4.5, y); ctx.lineTo(x, y + 4.5); ctx.lineTo(x - 4.5, y); ctx.closePath(); if (name === "flip") ctx.fill(); else ctx.stroke(); }
        ctx.globalAlpha = 1;
      }
      const at = l.segs.find((s) => s[0] <= this.s.clock && this.s.clock < s[1]);
      if (at && lastY != null) {
        const x0 = this.xOf(at[0]) ?? 0, x1 = this.xOf(Math.min(end, at[1])) ?? w;
        this.hits.push({ uid: l.uid, y: lastY, x0, x1 });
        if (!l.node && !faint) labels.push({ y: lastY, text: l.label, color: lastColor });
      }
    }
    this.labels = labels.map((l) => ({ ...l, pri: 1 }));
  }

  private drawTop(ctx: CanvasRenderingContext2D, w: number, _h: number) {
    const end = this.end();
    // touches and their outcomes
    if (this.s.layers.touches) for (const e of this.s.touches) {
      if (e.t > end || e.price == null) continue;
      const x = this.xOf(e.t), y = this.yOf(e.price);
      if (x == null || y == null || x < -10 || x > w + 10) continue;
      if (e.own) {
        ctx.fillStyle = CREAM; ctx.strokeStyle = "#111"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(x, y - 7); ctx.lineTo(x + 7, y); ctx.lineTo(x, y + 7); ctx.lineTo(x - 7, y); ctx.closePath(); ctx.fill(); ctx.stroke();
        continue;
      }
      ctx.lineWidth = 1.6;
      if (e.label === "held") { ctx.fillStyle = MARK.held; ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill(); }
      else if (e.label === "broke") { ctx.strokeStyle = MARK.broke; ctx.beginPath(); ctx.moveTo(x - 3.5, y - 3.5); ctx.lineTo(x + 3.5, y + 3.5); ctx.moveTo(x + 3.5, y - 3.5); ctx.lineTo(x - 3.5, y + 3.5); ctx.stroke(); }
      else if (e.label === "timeout") { ctx.strokeStyle = MARK.timeout; ctx.setLineDash([2, 2]); ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]); }
      else { ctx.strokeStyle = "rgba(180,180,180,.55)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.stroke(); }
    }
    // King switches: crowns
    if (this.s.layers.nodes) for (const [t, , px] of this.s.kings) {
      if (t > end) continue;
      const x = this.xOf(t), y = this.yOf(px);
      if (x == null || y == null || x < -10 || x > w + 10) continue;
      crown(ctx, x, y - 9);
    }
    // the proposed trade from the decision on, and once revealed its fill and exit
    const tradeLabels: { y: number; text: string; color: string; pri: number }[] = [];
    const tr = this.s.trade;
    if (tr && this.s.clock + 60 >= tr.from) {
      const x0 = this.xOf(tr.from) ?? 0;
      const line = (p: number | null, color: string, text: string, dash: number[]) => {
        if (p == null) return;
        const y = this.yOf(p);
        if (y == null) return;
        ctx.strokeStyle = color; ctx.lineWidth = 1.25; ctx.setLineDash(dash);
        ctx.beginPath(); ctx.moveTo(x0, Math.round(y) + 0.5); ctx.lineTo(w, Math.round(y) + 0.5); ctx.stroke(); ctx.setLineDash([]);
        tradeLabels.push({ y, text, color, pri: 0 });
      };
      line(tr.target, "rgba(95,211,155,.95)", "target", [6, 3]);
      line(tr.stop, "rgba(255,79,79,.95)", "stop", [6, 3]);
      if (tr.entry != null) line(tr.entry, CREAM, "entry", [2, 3]);
      for (const p of [tr.fill, tr.exit]) {
        if (!p || p.t > end) continue;
        const x = this.xOf(p.t), y = this.yOf(p.px);
        if (x == null || y == null) continue;
        const exit = p === tr.exit;
        const good = exit && (tr.exit!.result === "target");
        ctx.fillStyle = exit ? (good ? MARK.held : tr.exit!.result === "stop" ? MARK.broke : MARK.timeout) : CREAM;
        ctx.strokeStyle = "#111"; ctx.lineWidth = 1.5;
        const up = exit ? tr.dir < 0 : tr.dir > 0;
        ctx.beginPath(); ctx.moveTo(x, y + (up ? -1 : 1) * 8); ctx.lineTo(x + 6, y); ctx.lineTo(x - 6, y); ctx.closePath(); ctx.fill(); ctx.stroke();
      }
    }
    // one label column right of the orbs: the trade's labels first, then the levels', skipping any that would collide
    const xc0 = this.xOf(this.s.clock + 60);
    if (xc0 != null) {
      const lx = xc0 + LABEL_GAP;
      ctx.font = FONT; ctx.textAlign = "left"; ctx.textBaseline = "middle";
      const placed: number[] = [];
      for (const lb of [...tradeLabels, ...this.labels].sort((a, b) => a.pri - b.pri)) {
        if (placed.some((y) => Math.abs(y - lb.y) < 12)) continue;
        placed.push(lb.y);
        const tw = ctx.measureText(lb.text).width;
        ctx.fillStyle = "rgba(12,12,12,.8)"; ctx.fillRect(lx - 3, lb.y - 7, tw + 6, 14);
        ctx.fillStyle = lb.color; ctx.fillText(lb.text, lx, lb.y + 0.5);
      }
    }
    // the relevant nodes at the clock: orbs right of its bar
    this.orbHits = [];
    if (this.s.layers.nodes) {
      const xc = this.xOf(this.s.clock + 60);
      if (xc != null) {
        const x = xc + ORB_GAP;
        const orbs = [...this.s.orbs].sort((a, b) => Number(a.king) - Number(b.king) || a.share - b.share);
        for (const o of orbs) {
          const y = this.yOf(o.price);
          if (y == null) continue;
          const r = 3 + 8 * Math.sqrt(Math.max(0, Math.min(1, o.share)));
          const col = o.value >= 0 ? PIKA : BARNEY;
          ctx.save();
          ctx.shadowColor = col; ctx.shadowBlur = o.king ? 16 : 9;
          ctx.globalAlpha = o.king ? 1 : 0.55 + 0.45 * Math.min(1, o.share);
          ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
          ctx.restore();
          if (o.king) {
            ctx.strokeStyle = KING_RING; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, r + 3.5, 0, Math.PI * 2); ctx.stroke();
          }
          this.orbHits.push({ ...o, x, y });
        }
      }
    }
  }

  autoscaleInfo() {
    const tr = this.s.trade;
    if (!tr) return null;
    const ps = [tr.stop, tr.target, tr.entry ?? tr.stop];
    return { priceRange: { minValue: Math.min(...ps), maxValue: Math.max(...ps) } };
  }
}

function crown(ctx: CanvasRenderingContext2D, x: number, y: number) {
  ctx.fillStyle = CREAM; ctx.strokeStyle = "#111"; ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x - 6, y + 4); ctx.lineTo(x - 6, y - 2); ctx.lineTo(x - 3, y + 1); ctx.lineTo(x, y - 4); ctx.lineTo(x + 3, y + 1); ctx.lineTo(x + 6, y - 2); ctx.lineTo(x + 6, y + 4);
  ctx.closePath(); ctx.fill(); ctx.stroke();
}
