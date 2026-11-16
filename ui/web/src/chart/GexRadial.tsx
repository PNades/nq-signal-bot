// The GEX snapshot radial (design-system/MASTER.md §3.6, §8, §11; picks.md; owner 2026-11-14): one map's board at the
// replay clock on a canvas, drawn as a dial. The kept strikes run from low (lower left) to high (lower right) around a
// 240° arc; each node is a spoke from the base ring out to its share of the board's largest |value|, Pika gold for +,
// Barney orchid for −, with `+` / `−` in every label, an orb at its tip sized by the same share. Rings mark 25% (F2.4's
// relevance line), 50% and 100%. Spot is the needle. The King is the brightest orb with an extra glow ring; nodes that
// are NQ / ES levels at the clock (relevant) are drawn full, the rest faint. Full: the spokes glide to the next
// minute's board (400 ms); Calm: they change in one step (MASTER §8: 1 update/s, no motion).
import { useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { MapName, MapSnap } from "@/pages/review/types";
import { gexText } from "./data";

const PIKA = "#F2B950", BARNEY = "#DD9BE0", KING_RING = "rgba(255,241,220,.7)", RING = "rgba(255,255,255,.08)", TEXT = "#b4b4b4";
const GLIDE_MS = 400;
const ARC_FROM = (210 * Math.PI) / 180, ARC_SWEEP = (240 * Math.PI) / 180;   // lower left, clockwise over the top, lower right
const BASE = 0.36;            // the base ring, as a share of the radius
const LABEL_SHARE = 0.25;     // labels on nodes at >= 25% of the largest (F2.4's relevance share), and the King
const RINGS = [0.25, 0.5, 1];

interface Spoke { key: number; ang: number; share: number; v: number; king: boolean; rel: boolean }

function geometry(size: number) {
  // the arc opens at the bottom, so its centre sits low and its radius uses the height
  const c = size / 2, R = size / 2 - 22;
  return { c, cy: c + R * 0.22, R, r0: R * BASE };
}
/** a spoke's length grows with the square root of its share, so a dominant King doesn't flatten every other node */
const reach = (share: number) => Math.sqrt(Math.max(0, Math.min(1, share)));

function spokes(snap: MapSnap, relevant: Set<number>): { list: Spoke[]; spotAng: number; lo: number; hi: number } {
  const ks = snap.s.map((x) => x[0]);
  const lo = Math.min(...ks, snap.spot), hi = Math.max(...ks, snap.spot);
  const ang = (k: number) => ARC_FROM - ARC_SWEEP * ((k - lo) / Math.max(1e-9, hi - lo));
  const list = snap.s.map(([k, v]) => ({ key: k, ang: ang(k), share: snap.max ? Math.min(1, Math.abs(v) / snap.max) : 0, v,
    king: snap.king != null && Math.abs(snap.king - k) < 1e-6, rel: relevant.has(k) }));
  return { list, spotAng: ang(snap.spot), lo, hi };
}

export function GexRadial({ maps, map, onMap, snap, relevant, unit, animated }: {
  maps: MapName[]; map: MapName; onMap: (m: MapName) => void; snap: MapSnap | null; relevant: Set<number>;
  unit: "NQ" | "ES"; animated: boolean;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState(340);
  const [hover, setHover] = useState<(Spoke & { x: number; y: number }) | null>(null);
  const shown = useRef<{ list: Spoke[]; spotAng: number } | null>(null);
  const tips = useRef<(Spoke & { x: number; y: number; r: number })[]>([]);
  const target = useMemo(() => (snap && snap.s.length ? spokes(snap, relevant) : null), [snap, relevant]);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize(Math.max(240, Math.min(380, Math.floor(el.clientWidth)))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const cv = canvas.current;
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = size * dpr; cv.height = size * dpr;
    const ctx = cv.getContext("2d")!;
    const from = new Map((shown.current?.list ?? []).map((o) => [o.key, o]));
    const fromSpot = shown.current?.spotAng;
    let raf = 0;
    const t0 = performance.now();
    const frame = (now: number) => {
      const k = animated && shown.current ? Math.min(1, (now - t0) / GLIDE_MS) : 1;
      const e = 1 - (1 - k) ** 3;
      const list = (target?.list ?? []).map((o) => {
        const a = from.get(o.key);
        return a ? { ...o, ang: a.ang + (o.ang - a.ang) * e, share: a.share + (o.share - a.share) * e } : o;
      });
      const spotAng = target ? (fromSpot != null ? fromSpot + (target.spotAng - fromSpot) * e : target.spotAng) : 0;
      tips.current = draw(ctx, dpr, size, list, spotAng, snap, unit, target);
      shown.current = { list, spotAng };
      if (k < 1) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [target, size, animated, snap, unit]);

  const onMove = (ev: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    const x = ev.clientX - r.left, y = ev.clientY - r.top;
    const hit = [...tips.current].reverse().find((o) => Math.hypot(o.x - x, o.y - y) < o.r + 4);
    setHover(hit ?? null);
  };

  return (
    <div className="flex flex-col gap-3" data-radial>
      <div className="flex items-center justify-between gap-2">
        <div className="eyebrow">GEX snapshot · at the replay clock</div>
        <div className="flex rounded-full border border-line-strong p-0.5" role="radiogroup" aria-label="Map">
          {maps.map((m) => (
            <button key={m} type="button" role="radio" aria-checked={map === m} onClick={() => onMap(m)} data-map={m}
              className={cn("h-6 rounded-full px-2 font-mono text-[11px]", map === m ? "bg-caramel text-cream" : "text-text-2 hover:text-text-1")}>{m}</button>
          ))}
        </div>
      </div>
      <div ref={wrap} className="relative mx-auto w-full" style={{ height: size, maxWidth: 380 }}>
        <canvas ref={canvas} style={{ width: size, height: size }} className="mx-auto block" onPointerMove={onMove} onPointerLeave={() => setHover(null)}
          role="img" aria-label={snap ? `${map} board: spot ${snap.spot}, King ${snap.king ?? "none"}` : `${map}: no board at this minute`} />
        {!snap && <div className="absolute inset-0 grid place-items-center text-[13px] text-text-3">No {map} board at this minute (boards start at 09:30).</div>}
        {hover && snap && (
          <div className="pointer-events-none absolute z-10 rounded-sm border border-line-strong bg-bg-3 px-3 py-2 text-[12px] shadow-[0_12px_32px_rgba(0,0,0,.55)]"
            style={{ left: Math.max(4, Math.min(hover.x + 12, size - 190)), top: Math.max(4, hover.y - 70) }} data-radial-tip>
            <div className="eyebrow">{map} {hover.key}{hover.king ? " · King" : hover.rel ? " · a level now" : ""}</div>
            <div className={cn("mt-1 font-mono", hover.v >= 0 ? "text-orb-pika" : "text-orb-barney")}>{gexText(hover.v)} {hover.v >= 0 ? "Pika" : "Barney"}</div>
            <div className="mt-0.5 font-mono text-text-2">{Math.round(hover.share * 100)}% of the largest · {unit} {(hover.key * snap.r).toLocaleString("en-US", { maximumFractionDigits: 2 })}</div>
          </div>
        )}
      </div>
      <p className="text-[12px] leading-4 text-text-3">Strikes run low → high around the dial; a spoke reaches its share of the largest node (rings at 25%, the relevance line, 50% and 100%; square-root scale). The needle is spot. Faint: not an {unit} level at this minute.</p>
    </div>
  );
}

function draw(ctx: CanvasRenderingContext2D, dpr: number, size: number, list: Spoke[], spotAng: number, snap: MapSnap | null, unit: string,
  target: ReturnType<typeof spokes> | null) {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);
  const { c, cy, R, r0 } = geometry(size);
  const out: (Spoke & { x: number; y: number; r: number })[] = [];
  if (!snap || !target) return out;
  const at = (ang: number, r: number) => [c + r * Math.cos(ang), cy - r * Math.sin(ang)] as const;
  // rings: the base and the shares
  ctx.lineWidth = 1;
  for (const s of [0, ...RINGS]) {
    const r = r0 + reach(s) * (R - r0);
    ctx.strokeStyle = s === 0.25 ? "rgba(255,224,194,.22)" : RING;
    ctx.setLineDash(s === 0.25 ? [3, 3] : []);
    ctx.beginPath(); ctx.arc(c, cy, r, -ARC_FROM, -ARC_FROM + ARC_SWEEP); ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.font = '10px "Geist Mono Variable", monospace'; ctx.fillStyle = "rgba(180,180,180,.6)"; ctx.textBaseline = "middle"; ctx.textAlign = "left";
  for (const s of RINGS) { const [x, y] = at(ARC_FROM - ARC_SWEEP, r0 + reach(s) * (R - r0)); ctx.fillText(`${s * 100}%`, x + 4, y + 7); }
  // the strike scale: its ends, below the arc's two ends
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (const k of [target.lo, target.hi]) { const [x, y] = at(k === target.lo ? ARC_FROM : ARC_FROM - ARC_SWEEP, r0); ctx.fillText(`${k}`, x, y + 8); }
  ctx.textBaseline = "middle";
  // spokes and orbs, small first
  for (const o of [...list].sort((a, b) => Number(a.king) - Number(b.king) || a.share - b.share)) {
    const col = o.v >= 0 ? PIKA : BARNEY;
    const rTip = r0 + reach(o.share) * (R - r0);
    const [x0, y0] = at(o.ang, r0), [x1, y1] = at(o.ang, rTip);
    const strong = o.rel || o.king;
    ctx.globalAlpha = strong ? 0.9 : 0.3;
    ctx.strokeStyle = col; ctx.lineWidth = strong ? 2 : 1.25;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    const r = 2 + 6 * Math.sqrt(o.share);
    ctx.save();
    ctx.shadowColor = col; ctx.shadowBlur = o.king ? 18 : strong ? 8 : 0;
    ctx.globalAlpha = strong ? 1 : 0.4;
    ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x1, y1, r, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
    if (o.king) { ctx.strokeStyle = KING_RING; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x1, y1, r + 4, 0, Math.PI * 2); ctx.stroke(); }
    out.push({ ...o, x: x1, y: y1, r });
  }
  ctx.globalAlpha = 1;
  // labels at the tips of the large nodes, pushed outwards, skipping one that would collide with a label already drawn
  ctx.font = '10px "Geist Mono Variable", monospace'; ctx.textBaseline = "middle";
  const placed: { x: number; y: number; w: number }[] = [];
  for (const o of [...out].sort((a, b) => Number(b.king) - Number(a.king) || b.share - a.share)) {
    if (!(o.king || o.share >= LABEL_SHARE)) continue;
    const text = `${o.key} ${gexText(o.v)}${o.king ? " King" : ""}`;
    const w = ctx.measureText(text).width;
    const right = Math.cos(o.ang) >= 0;
    const lx = o.x + (right ? 1 : -1) * (o.r + 6), ly = o.y;
    const box = { x: right ? lx : lx - w, y: ly, w };
    if (placed.some((p) => Math.abs(p.y - box.y) < 11 && box.x < p.x + p.w + 4 && p.x < box.x + box.w + 4)) continue;
    if (box.x < 2 || box.x + w > size - 2) continue;
    placed.push(box);
    ctx.textAlign = right ? "left" : "right";
    ctx.fillStyle = o.king ? "#FFF1DC" : TEXT;
    ctx.fillText(text, lx, ly);
  }
  // spot: the needle and the centre readout
  const [nx, ny] = at(spotAng, r0 - 2);
  ctx.strokeStyle = "#FFE0C2"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(c, cy); ctx.lineTo(nx, ny); ctx.stroke();
  ctx.fillStyle = "#FFE0C2"; ctx.beginPath(); ctx.arc(c, cy, 3, 0, Math.PI * 2); ctx.fill();
  ctx.textAlign = "center"; ctx.textBaseline = "top"; ctx.fillStyle = "#eeeeee";
  ctx.font = '12px "Geist Mono Variable", monospace';
  ctx.fillText(`spot ${snap.spot}`, c, cy + 10);
  ctx.fillStyle = TEXT; ctx.font = '10px "Geist Mono Variable", monospace';
  ctx.fillText(`≈ ${unit} ${(snap.spot * snap.r).toLocaleString("en-US", { maximumFractionDigits: 0 })}`, c, cy + 26);
  return out;
}
