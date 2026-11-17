// The main chart's own drawing, as one Lightweight Charts series primitive (MASTER.md §3.6, §11; SPEC §9b item 3):
// - zones (merged levels) as shaded bands between their edges, hatched while provisional, coloured by the role;
// - single levels as thin lines, solid in the original role, dashed while provisional, a marker at each cross (◇),
//   flip (◆) and reclaim (○); GEX nodes are not drawn as lines but as orbs;
// - the GEX orbs across the whole chart (owner 2026-11-16, stage-4 review (3); the reference
//   docs/design/inspiration/gex_chart_1.jpeg): at every minute, one small orb per node at its price on the chart
//   (strike × the R in force), from the board known at that minute only, sized and lit by the node's share of that
//   board's largest |value|; Pika gold (+), Barney orchid (−), the King the brightest;
// - touches with their outcome once known (● held, ✕ broke, ◌ timeout), the card's own touch as a cream ◆;
// - the confirmed King's switches as crowns; the proposed trade (entry, stop, target from the decision on) and, once
//   revealed, its fill and exit.
// Everything is clipped at `limit` (the decision time until the owner answers) and at the replay clock. Every drawn
// level, high / low, node orb and touch is recorded for the pointer (`hitsAt`), so hovering shows its facts and price;
// zones, crowns and the trade lines are not (owner 2026-11-16, round 3: the setup's zone is in the panel on the right);
// nothing is written on the price axis except the trade's and the last price's own tags (MainChart).
import type { CanvasRenderingTarget2D } from "fancy-canvas";
import type { IChartApi, IPrimitivePaneRenderer, IPrimitivePaneView, ISeriesApi, ISeriesPrimitive, Logical, PrimitivePaneViewZOrder, SeriesAttachedParameter, Time } from "lightweight-charts";
import type { Level, MapSnap, Touch, Zone } from "@/pages/review/types";
import { roleAt } from "./data";

export interface TradeLines { from: number; entry: number | null; stop: number; target: number; dir: number; fill?: { t: number; px: number } | null; exit?: { t: number; px: number; result: string } | null }
export interface OrbSeries { chain: string; snaps: MapSnap[] }
export interface OverlayState {
  clock: number;      // the replay minute (UTC s, the bar's start)
  limit: number;      // nothing drawn after this (UTC s)
  barTimes: number[]; // the bars on the chart (UTC s), sorted
  levels: Level[];
  zones: Zone[];
  touches: Touch[];
  kings: [number, number, number, string][];  // t, strike, chart price, chain
  orbs: OrbSeries[];
  trade: TradeLines | null;
  layers: Record<string, boolean>;
  hover: string | null;   // a level uid under the pointer (drawn brighter)
  /** the setup's own levels and zone (drawn at full strength); with `focus` on, every other one is faint context */
  focusUids: Set<string>;
  ownZone: string | null;
  focus: boolean;
}

/** one drawn object under the pointer: what it is, its price (for sorting), and its facts */
export interface Hit { key: string; kind: "zone" | "level" | "orb" | "touch" | "king" | "trade"; price: number; title: string; lines: string[]; color: string }
type Shape = { kind: "h"; x0: number; x1: number; y: number } | { kind: "r"; x0: number; x1: number; y0: number; y1: number } | { kind: "c"; x: number; y: number; r: number };

export const ROLE_COLOR = { ceiling: "rgba(255,123,110,.78)", floor: "rgba(95,211,155,.78)", none: "rgba(179,173,163,.6)" };
const ZONE_FILL = { ceiling: "rgba(255,123,110,.10)", floor: "rgba(95,211,155,.10)", none: "rgba(179,173,163,.08)" };
const CONTEXT_ALPHA = 0.28;      // levels outside the setup, with Focus on
const CONTEXT_ZONE_ALPHA = 0.14; // zones outside the setup (they stack, so quieter than the lines)
const PIKA = [242, 185, 80], BARNEY = [221, 155, 224], CREAM = "#FFE0C2";
const MARK = { held: "#5FD39B", broke: "#FF4F4F", timeout: "#B4B4B4" };
export const RIGHT_ROOM_PX = 72;  // empty room right of the last bar (the owner reads candle tops there)
const HIT_PX = 4;                // the pointer's tolerance for lines and markers
const fmtP = (p: number) => p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const gexTxt = (v: number) => { const a = Math.abs(v), s = v < 0 ? "−" : "+"; return a >= 1e6 ? `${s}${(a / 1e6).toFixed(1)}M` : a >= 1e3 ? `${s}${(a / 1e3).toFixed(0)}K` : `${s}${a.toFixed(0)}`; };

function bisectRight(a: number[], t: number) {
  let lo = 0, hi = a.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= t) lo = m + 1; else hi = m; }
  return lo;
}

/** a pre-rendered orb (a vertical oval with its glow), drawn once per look and blitted thousands of times */
const sprites = new Map<string, HTMLCanvasElement>();
function sprite(rgb: number[], rx: number, ry: number, alpha: number, glow: number, king: boolean, pr: number): HTMLCanvasElement {
  const key = `${rgb.join()}|${rx}|${ry}|${alpha}|${glow}|${king}|${pr}`;
  let c = sprites.get(key);
  if (c) return c;
  const pad = Math.ceil((glow + (king ? 3 : 0) + 2) * pr), X = rx * pr, Y = ry * pr;
  const w = Math.ceil(2 * (X + pad)), h = Math.ceil(2 * (Y + pad));
  c = document.createElement("canvas");
  c.width = w; c.height = h;
  const g = c.getContext("2d")!;
  if (glow > 0) { g.shadowColor = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${Math.min(1, alpha + 0.2)})`; g.shadowBlur = glow * pr; }
  g.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`; g.beginPath(); g.ellipse(w / 2, h / 2, X, Y, 0, 0, Math.PI * 2); g.fill();
  if (king) { g.shadowBlur = 0; g.strokeStyle = "rgba(255,241,220,.85)"; g.lineWidth = 1 * pr; g.beginPath(); g.ellipse(w / 2, h / 2, X + 1.4 * pr, Y + 1.4 * pr, 0, 0, Math.PI * 2); g.stroke(); }
  sprites.set(key, c);
  return c;
}
/** an orb's half width and half height (px) for its share of the board's largest node and the bar spacing: a weak node
 * fills most of its minute's width (its row reads as a near-continuous line), every orb is taller than it is wide, and
 * both grow with the share (owner 2026-11-16, round 3; the reference docs/design/inspiration/gex_chart_1.jpeg) */
export function orbSize(share: number, spacing: number): [number, number] {
  const q = Math.sqrt(Math.max(0, Math.min(1, share)));
  const rx = Math.max(0.6, Math.min(4, spacing * 0.42 * (0.85 + 0.35 * q)));
  const ry = Math.max(rx * 1.15, Math.min(8, rx * 1.15 + 2.6 * q));
  return [Math.round(rx * 4) / 4, Math.round(ry * 4) / 4];
}

/** "12:40" in New York from a UTC second (DST-aware) */
const ET_CLOCK = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
function clockET(t: number) { return ET_CLOCK.format(new Date(t * 1000)); }

export class ReviewOverlay implements ISeriesPrimitive<Time> {
  s: OverlayState;
  chart: IChartApi | null = null;
  series: ISeriesApi<"Candlestick"> | null = null;
  private request: (() => void) | null = null;
  private hatch = new Map<string, CanvasPattern | null>();
  private shapes: { shape: Shape; hit: Hit | (() => Hit) }[] = [];
  /** each level's role-change instants (for a binary search per segment) */
  private roleTimes = new WeakMap<Level, number[]>();
  private orbShapes: { x: number; y: number; r: number; rx: number; ry: number; hit: () => Hit }[] = [];
  private orbCache: { key: string; canvas: HTMLCanvasElement } | null = null;
  /** frames that had to draw the orbs again (the cache missed): read by the stage-4 shoot script */
  orbRedraws = 0;
  /** each layer's drawing time (ms: orbs, the rest), the last few hundred: read by the stage-4 shoot script */
  drawMs: [string, number, number][] = [];

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
  /** the right edge of the last bar shown: right of it the pointer shows nothing (the owner reads candle tops there) */
  lastBarRight(): number | null { const bt = this.s.barTimes; return bt.length ? this.xOf(bt[bt.length - 1] + 60) : null; }

  /** every drawn object within the pointer's tolerance at (x, y), sorted by price, highest first */
  hitsAt(x: number, y: number): Hit[] {
    const out = new Map<string, Hit>();
    for (const { shape: s, hit } of this.shapes) {
      const on = s.kind === "h" ? x >= s.x0 - HIT_PX && x <= s.x1 + HIT_PX && Math.abs(y - s.y) <= HIT_PX
        : s.kind === "r" ? x >= s.x0 && x <= s.x1 && y >= s.y0 - 1 && y <= s.y1 + 1
          : Math.hypot(x - s.x, y - s.y) <= s.r + HIT_PX;
      if (!on) continue;
      const h = typeof hit === "function" ? hit() : hit;
      if (!out.has(h.key)) out.set(h.key, h);
    }
    for (const o of this.orbShapes) {
      const dx = (x - o.x) / (o.rx + 2), dy = (y - o.y) / (o.ry + 2);
      if (dx * dx + dy * dy <= 1) { const h = o.hit(); if (!out.has(h.key)) out.set(h.key, h); }
    }
    return [...out.values()].sort((a, b) => b.price - a.price);
  }

  paneViews(): readonly IPrimitivePaneView[] {
    const view = (z: PrimitivePaneViewZOrder, draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void, orbs = false): IPrimitivePaneView => ({
      zOrder: () => z,
      renderer: (): IPrimitivePaneRenderer => ({
        draw: (target: CanvasRenderingTarget2D) => {
          const t0 = performance.now();
          if (orbs) target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio }) => this.drawOrbs(context, bitmapSize.width, bitmapSize.height, horizontalPixelRatio));
          const t1 = performance.now();
          target.useMediaCoordinateSpace(({ context, mediaSize }) => draw(context, mediaSize.width, mediaSize.height));
          this.drawMs.push([z, t1 - t0, performance.now() - t1]);
          if (this.drawMs.length > 600) this.drawMs.splice(0, 300);
        },
      }),
    });
    return [view("bottom", (c, w, h) => this.drawZones(c, w, h)), view("normal", (c, w, h) => this.drawLevels(c, w, h)), view("top", (c, w, h) => this.drawTop(c, w, h), true)];
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

  /** the times on screen (UTC s, a bar's margin each side): only what falls inside is drawn */
  private visibleTimes(): [number, number] {
    const bt = this.s.barTimes, vis = this.chart?.timeScale().getVisibleLogicalRange();
    if (!bt.length || !vis) return [-Infinity, Infinity];
    const a = Math.max(0, Math.floor(vis.from) - 1), b = Math.ceil(vis.to) + 1;
    return [bt[Math.min(a, bt.length - 1)], b >= bt.length ? Infinity : bt[b]];
  }

  private drawZones(ctx: CanvasRenderingContext2D, w: number, _h: number) {
    this.shapes = [];                 // the first layer of a frame: the pointer's objects are recorded afresh
    if (!this.s.layers.zones) return;
    const end = this.end(), [vf, vt] = this.visibleTimes();
    for (const z of this.s.zones) {
      const pts = z.pts;
      for (let i = 0; i < pts.length; i++) {
        const p = pts[i];
        if (p[0] > end || p[0] > vt || p[1] == null || p[2] == null) continue;
        if (p[3] < 2 && !z.own) continue;            // a single level is a line, not a band (owner 2026-11-14)
        const t1 = Math.min(end, i + 1 < pts.length ? pts[i + 1][0] : z.end ?? end);
        if (t1 < vf) continue;
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
    const end = this.end(), L = this.s.layers, [vf, vt] = this.visibleTimes();
    for (const l of this.s.levels) {
      if (l.node) continue;                        // GEX nodes are drawn as orbs (owner 2026-11-16)
      const layer = l.group === "highs/lows" ? "highs" : "lines";
      if (!L[layer]) continue;
      const hot = this.s.hover === l.uid;
      const faint = this.s.focus && !hot && !this.s.focusUids.has(l.uid);
      // the segments on screen only (they are in time order)
      let lo = 0, hi = l.segs.length;
      while (lo < hi) { const m = (lo + hi) >> 1; if (l.segs[m][1] < vf) lo = m + 1; else hi = m; }
      // one path per stretch with the same role look (a stroke per minute was the replay's bottleneck)
      let look = "", open = false;
      const flush = () => { if (open) { ctx.stroke(); open = false; } };
      ctx.lineWidth = hot ? 2 : 1;
      ctx.globalAlpha = faint ? CONTEXT_ALPHA : 1;
      let rt = this.roleTimes.get(l);
      if (!rt) { rt = l.roles.map((r) => r[0]); this.roleTimes.set(l, rt); }
      for (let si = lo; si < l.segs.length; si++) {
        const s = l.segs[si];
        if (s[0] >= end || s[0] > vt) break;
        const t1 = Math.min(end, s[1]);
        const y = this.yOf((s[2] + s[3]) / 2);
        if (y == null) continue;
        const yy = Math.round(y) + 0.5;
        // the role steps inside this segment (usually none): the segment is cut at each
        let ri = bisectRight(rt, s[0]);
        let a = s[0];
        while (a < t1) {
          const b = ri < rt.length && rt[ri] < t1 ? rt[ri] : t1;
          const rIdx = bisectRight(rt, a) - 1;
          const r = rIdx >= 0 ? l.roles[rIdx] : null;
          const segA = a;
          a = b;
          if (b < t1) ri++;
          const x0 = this.xOf(segA), x1 = this.xOf(b);
          if (x0 == null || x1 == null || x1 < 0 || x0 > w) continue;
          const lk = `${r?.[1] ?? "none"}|${r?.[2] ? 1 : 0}`;
          if (lk !== look) {
            flush();
            look = lk;
            ctx.strokeStyle = ROLE_COLOR[r?.[1] ?? "none"];
            ctx.setLineDash(r?.[2] ? [5, 4] : []);
          }
          if (!open) { ctx.beginPath(); open = true; ctx.moveTo(x0, yy); } else ctx.lineTo(x0, yy);
          ctx.lineTo(x1, yy);
          const lo2 = s[2], hi2 = s[3];
          this.shapes.push({ shape: { kind: "h", x0, x1, y }, hit: () => this.levelHitNew(l, lo2, hi2, end) });
        }
      }
      flush();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      // cross / flip / reclaim markers on the line
      for (const [t, name] of l.marks) {
        if (t > end) break;
        if (t < vf || t > vt) continue;
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
    }
  }

  private levelHitNew(l: Level, lo: number, hi: number, end: number): Hit {
    const r = roleAt(l.roles, end), mid = (lo + hi) / 2;
    const state = !r ? "no role yet" : `${r[1]}${r[2] ? (r[3] ? ", provisional, crossed back" : ", provisional") : r[4] ? `, flipped ×${r[4]}` : ""}`;
    return { key: `l:${l.uid}`, kind: "level", price: mid, color: ROLE_COLOR[r?.[1] ?? "none"], title: l.label,
      lines: [lo === hi ? fmtP(mid) : `${fmtP(mid)} (band ${fmtP(lo)} – ${fmtP(hi)})`, state] };
  }

  /** the orbs: every minute's board up to the clock, at each node's chart price; cached while nothing on screen moves */
  private drawOrbs(ctx: CanvasRenderingContext2D, W: number, H: number, pr: number) {
    if (!this.s.layers.nodes || !this.chart || !this.series) { this.orbShapes = []; return; }
    const ts = this.chart.timeScale(), vis = ts.getVisibleLogicalRange();
    const key = [vis?.from, vis?.to, this.yOf(10000), this.yOf(20000), this.s.clock, this.s.limit, W, H, pr, this.s.barTimes.length].map(String).join("|");
    if (this.orbCache?.key !== key || this.orbCache.canvas.dataset.src !== this.orbSrc()) {
      this.orbRedraws++;
      const c = this.orbCache?.canvas ?? document.createElement("canvas");
      c.width = W; c.height = H; c.dataset.src = this.orbSrc();
      const g = c.getContext("2d")!;
      g.clearRect(0, 0, W, H);
      const shapes: typeof this.orbShapes = [];
      const bt = this.s.barTimes;
      const xa = bt.length ? this.xOf(bt[bt.length - 1]) : null, xb = bt.length ? this.xOf(bt[bt.length - 1] + 60) : null;
      const spacing = xa != null && xb != null ? Math.max(1, xb - xa) : 6;
      const tFrom = vis && bt.length ? bt[Math.max(0, Math.min(bt.length - 1, Math.floor(vis.from)))] : 0;
      const last = Math.min(this.s.clock, this.s.limit);
      for (const ser of this.s.orbs) {
        let i0 = 0, i1 = ser.snaps.length;
        while (i0 < i1) { const m = (i0 + i1) >> 1; if (ser.snaps[m].t + 60 < tFrom) i0 = m + 1; else i1 = m; }
        for (let si = i0; si < ser.snaps.length; si++) {
          const sn = ser.snaps[si];
          if (sn.t > last) break;
          const x = this.xOf(sn.t + 30);
          if (x == null || x < -6 || x > W / pr + 6) continue;
          for (const [k, v] of sn.s) {
            const price = k * sn.r, y = this.yOf(price);
            if (y == null || y < -6 || y > H / pr + 6) continue;
            const share = sn.max ? Math.min(1, Math.abs(v) / sn.max) : 0;
            const king = sn.king != null && Math.abs(sn.king - k) < 1e-6;
            const [rx, ry] = orbSize(share, spacing);
            const r = Math.max(rx, ry);
            const alpha = king ? 1 : Math.round((0.22 + 0.7 * Math.pow(share, 0.6)) * 10) / 10;
            const glow = king ? 5 : share >= 0.5 ? 3 : share >= 0.25 ? 1.5 : 0;
            const sp = sprite(v >= 0 ? PIKA : BARNEY, rx, ry, alpha, glow, king, pr);
            g.drawImage(sp, Math.round(x * pr - sp.width / 2), Math.round(y * pr - sp.height / 2));
            const t = sn.t, chain = ser.chain;
            shapes.push({ x, y, r, rx, ry, hit: () => ({ key: `o:${chain}:${k}:${t}`, kind: "orb", price, color: v >= 0 ? "#F2B950" : "#DD9BE0",
              title: `${chain} ${k}${king ? " · King" : ""}`,
              lines: [fmtP(price), `${gexTxt(v)} ${v >= 0 ? "Pika" : "Barney"} · ${Math.round(share * 100)}% of the largest`, `board at ${clockET(t + 60)} ET`] }) });
          }
        }
      }
      this.orbCache = { key, canvas: c };
      this.orbShapes = shapes;
    }
    ctx.drawImage(this.orbCache.canvas, 0, 0);
  }

  /** which boards the cached orbs were drawn from (a new card or instrument draws them again) */
  private orbSrc(): string {
    return this.s.orbs.map((o) => `${o.chain}:${o.snaps.length}:${o.snaps[0]?.t ?? 0}`).join(",");
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
      } else {
        ctx.lineWidth = 1.6;
        if (e.label === "held") { ctx.fillStyle = MARK.held; ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill(); }
        else if (e.label === "broke") { ctx.strokeStyle = MARK.broke; ctx.beginPath(); ctx.moveTo(x - 3.5, y - 3.5); ctx.lineTo(x + 3.5, y + 3.5); ctx.moveTo(x + 3.5, y - 3.5); ctx.lineTo(x - 3.5, y + 3.5); ctx.stroke(); }
        else if (e.label === "timeout") { ctx.strokeStyle = MARK.timeout; ctx.setLineDash([2, 2]); ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]); }
        else { ctx.strokeStyle = "rgba(180,180,180,.55)"; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.stroke(); }
      }
      this.shapes.push({ shape: { kind: "c", x, y, r: e.own ? 7 : 4 }, hit: { key: `t:${e.t}:${e.price}`, kind: "touch", price: e.price,
        color: e.own ? CREAM : (e.label && MARK[e.label as keyof typeof MARK]) || "#b4b4b4",
        title: e.own ? `This setup's touch · ${clockET(e.t)} ET` : `Touch · ${clockET(e.t)} ET`,
        lines: [fmtP(e.price), `${e.role}, ${e.dir > 0 ? "long" : "short"}`, e.own ? "outcome hidden until you answer" : e.label ? `structure: ${e.label}` : "outcome not known yet"] } });
    }
    // King switches: crowns
    if (this.s.layers.nodes) for (const [t, strike, px, chain] of this.s.kings) {
      if (t > end) continue;
      const x = this.xOf(t), y = this.yOf(px);
      if (x == null || y == null || x < -10 || x > w + 10) continue;
      crown(ctx, x, y - 9);
      void strike; void chain;
    }
    // the proposed trade from the decision on, and once revealed its fill and exit
    const tr = this.s.trade;
    if (tr && this.s.clock + 60 >= tr.from) {
      const x0 = this.xOf(tr.from) ?? 0;
      const line = (p: number | null, color: string, name: string, dash: number[]) => {
        if (p == null) return;
        const y = this.yOf(p);
        if (y == null) return;
        ctx.strokeStyle = color; ctx.lineWidth = 1.25; ctx.setLineDash(dash);
        ctx.beginPath(); ctx.moveTo(x0, Math.round(y) + 0.5); ctx.lineTo(w, Math.round(y) + 0.5); ctx.stroke(); ctx.setLineDash([]);
        void name;
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
