// The three Home title concepts (design-system/pages/home.md). Each one forms the CreamFlow title,
// calls onSettled once it is formed, idles, and reacts to the cursor. `still` = Calm: draw the formed
// title once, no loop. `skip` = jump to the formed state at once.
import { useEffect, useRef, useState } from "react";
import { LiquidText } from "@/components/ui/liquid-text";
import type { TitleLayout } from "./titleLayout";

/** `layout`: the shared title geometry (order flow uses it; re-laid out on every size change). */
export interface ConceptProps { skip: boolean; still: boolean; onSettled: () => void; layout?: TitleLayout | null }

const CREAM = "#F3E3C3";
const GOLD = "#E7BF72";
const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

export function useFontsReady() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let live = true;
    Promise.all([
      document.fonts.load('400 120px "Instrument Serif"', "CreamFlow"),
      document.fonts.load('italic 400 120px "Instrument Serif"', "Flow"),
    ]).finally(() => live && setReady(true));
    return () => { live = false; };
  }, []);
  return ready;
}

// ─── 1. Cream pour ─────────────────────────────────────────────────────────────
// 21st.dev Liquid Text (isaiahbjork, id 43190) in cream on black; the forming intro is added on top:
// a thin stream of cream falls into the word and the letters fill from the bottom with a wavy surface.
export function PourTitle({ skip, still, onSettled }: ConceptProps) {
  const fillRef = useRef<HTMLDivElement>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  const done = useRef(false);
  useEffect(() => {
    const fill = fillRef.current, stream = streamRef.current;
    if (!fill || !stream) return;
    const finish = () => {
      fill.style.clipPath = "none";
      stream.style.opacity = "0";
      if (!done.current) { done.current = true; onSettled(); }
    };
    if (still || skip) { finish(); return; }
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      const t = (now - t0) / 1000;
      const w = fill.clientWidth, h = fill.clientHeight;
      const lvl = easeInOut(clamp((t - 0.45) / 1.45)) * 1.12;
      const surface = h * (0.86 - lvl * 0.86) + h * 0.12;
      const amp = 7 * (1 - clamp(lvl)) + 1.5;
      const cx = w * 0.5;
      const pts: string[] = [];
      for (let i = 0; i <= 40; i++) {
        const x = (w * i) / 40;
        const mound = -22 * Math.exp(-Math.pow((x - cx) / 70, 2)) * (1 - clamp(lvl));
        const y = surface + amp * Math.sin((x / w) * Math.PI * 5 + t * 5.5) + mound;
        pts.push(`${x.toFixed(1)}px ${y.toFixed(1)}px`);
      }
      fill.style.clipPath = `polygon(0px ${h}px, ${pts.join(", ")}, ${w}px ${h}px)`;
      // the stream: falls from above, pours, then its top end drops in and it is gone
      const fallTop = -260;
      const head = Math.min(surface, fallTop + easeOut(clamp(t / 0.5)) * (surface - fallTop));
      const tail = t < 1.55 ? fallTop : fallTop + easeInOut(clamp((t - 1.55) / 0.45)) * (surface - fallTop);
      stream.style.top = `${tail}px`;
      stream.style.height = `${Math.max(0, head - tail)}px`;
      stream.style.width = `${8 - 4 * clamp((t - 1.2) / 0.8)}px`;
      stream.style.opacity = t > 2.05 ? "0" : "1";
      if (t > 2.15) { finish(); return; }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [skip, still, onSettled]);

  return (
    <div className="relative w-full">
      <div ref={streamRef} aria-hidden="true" className="pointer-events-none absolute left-1/2 z-10 -translate-x-1/2 rounded-full"
        style={{ background: "linear-gradient(180deg, rgba(243,227,195,0) 0%, #F3E3C3 35%, #FBF1DC 100%)", boxShadow: "0 0 18px rgba(243,227,195,.35)" }} />
      <div ref={fillRef} style={{ clipPath: "inset(100% 0 0 0)" }}>
        <LiquidText text="CreamFlow" color={CREAM} highlight="#FFF8EA" accentColor={GOLD} fontFamily='"Instrument Serif", Georgia, serif'
          fontWeight={400} fontSize={230} viscosity={0.62} autoplay={!still} frozen={false} ariaLabel="CreamFlow" className="w-full" />
      </div>
    </div>
  );
}

// ─── shared ─────────────────────────────────────────────────────────────────────
function cssColor(name: string, fallback: string) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return /^#[0-9a-f]{6}$/i.test(v) ? v : fallback;
}

function hexRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

// ─── 2. Order flow ─────────────────────────────────────────────────────────────
// Tick particles stream in from the edges like a tape and lock into the letters; a slow flow field moves
// through them at rest; the cursor pushes them aside. Brand colours only (owner 2026-11-14): gold from the
// left, lavender from the right, some cream; green and red stay reserved for P&L and status.
export function FlowTitle({ skip, still, onSettled, layout }: ConceptProps) {
  const ref = useRef<HTMLCanvasElement>(null);
  const skipRef = useRef(skip);
  skipRef.current = skip;
  // once the intro has played, a re-layout (window resize, sidebar collapse) shows the formed title at once
  const formed = useRef(false);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !layout) return;
    const { W, H, xs, ys, flow } = layout;
    const dpr = Math.min(2, devicePixelRatio || 1);
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
    const ctx = canvas.getContext("2d")!;
    ctx.scale(dpr, dpr);
    const n = xs.length;
    const sx = new Float32Array(n), sy = new Float32Array(n), delay = new Float32Array(n), side = new Uint8Array(n);
    const ox = new Float32Array(n), oy = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      side[i] = Math.random() < 0.5 ? 0 : 1;
      sx[i] = side[i] === 0 ? -20 - Math.random() * 120 : W + 20 + Math.random() * 120;
      sy[i] = ys[i] + (Math.random() - 0.5) * 260;
      const progress = side[i] === 0 ? xs[i] / W : 1 - xs[i] / W;
      delay[i] = progress * 0.7 + Math.random() * 0.45;
    }
    const CR = hexRgb(cssColor("--color-cream", CREAM)), GD = hexRgb(cssColor("--color-gold", GOLD));
    const LAV = hexRgb(cssColor("--color-orb-barney", "#DD9BE0"));
    const tint = new Uint8Array(n);
    for (let i = 0; i < n; i++) tint[i] = Math.random() < 0.22 ? 2 : side[i]; // 0 gold, 1 lavender, 2 cream
    const SRC = [GD, LAV, CR];
    const tapeN = 70;
    const tape = Array.from({ length: tapeN }, () => ({ x: Math.random() * W, y: Math.random() * H, v: 30 + Math.random() * 70, s: Math.random() < 0.5 ? 1 : -1 }));
    let mx = -9999, my = -9999;
    const onMove = (e: PointerEvent) => { const r = canvas.getBoundingClientRect(); mx = e.clientX - r.left; my = e.clientY - r.top; };
    const onLeave = () => { mx = my = -9999; };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerleave", onLeave);
    const DUR = 1.05;
    let settled = false, raf = 0, last = performance.now();
    const t0 = performance.now();
    let skipOffset = formed.current ? 10 : 0;
    const draw = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      if (skipRef.current && !settled) skipOffset = 10;
      const t = (now - t0) / 1000 + skipOffset;
      ctx.clearRect(0, 0, W, H);
      // the background tape keeps flowing faintly
      if (!still) for (const p of tape) {
        p.x += p.v * p.s * dt; if (p.x > W + 10) p.x = -10; if (p.x < -10) p.x = W + 10;
        const c = p.s > 0 ? GD : LAV;
        ctx.fillStyle = `rgba(${c[0]},${c[1]},${c[2]},0.2)`;
        ctx.fillRect(p.x, p.y, 2, 1.4);
      }
      let all = true;
      for (let i = 0; i < n; i++) {
        const p = still ? 1 : clamp((t - delay[i]) / DUR);
        if (p < 1) all = false;
        if (p <= 0) continue;
        const e = easeInOut(p);
        let x = lerp(sx[i], xs[i], e), y = lerp(sy[i], ys[i], e) + Math.sin(p * Math.PI * 3 + i) * 10 * (1 - e);
        if (!still && p >= 1) {
          const fx = 1.4 * Math.sin(ys[i] * 0.035 + t * 0.9) + 0.9 * Math.sin(xs[i] * 0.021 - t * 0.6);
          const fy = 1.2 * Math.cos(xs[i] * 0.03 + t * 0.8);
          let tx = fx, ty = fy;
          const dx = x - mx, dy = y - my, d = Math.hypot(dx, dy);
          if (d < 80) { const k = (80 - d) / 80; tx += (dx / (d || 1)) * k * 26; ty += (dy / (d || 1)) * k * 26; }
          ox[i] += (tx - ox[i]) * Math.min(1, dt * 7); oy[i] += (ty - oy[i]) * Math.min(1, dt * 7);
          x += ox[i]; y += oy[i];
        }
        const lock = clamp((p - 0.62) / 0.38);
        const from = SRC[tint[i]], to = flow[i] ? GD : CR;
        const r = lerp(from[0], to[0], lock), g = lerp(from[1], to[1], lock), b = lerp(from[2], to[2], lock);
        ctx.fillStyle = `rgba(${r | 0},${g | 0},${b | 0},${0.55 + 0.45 * lock})`;
        ctx.fillRect(x - 1.2, y - 1.2, 2.5, 2.5);
      }
      if (all && !settled) { settled = true; formed.current = true; onSettled(); }
      if (!still) raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    const onVis = () => { if (document.visibilityState === "hidden") cancelAnimationFrame(raf); else if (!still) { last = performance.now(); raf = requestAnimationFrame(draw); } };
    document.addEventListener("visibilitychange", onVis);
    return () => { cancelAnimationFrame(raf); window.removeEventListener("pointermove", onMove); window.removeEventListener("pointerleave", onLeave); document.removeEventListener("visibilitychange", onVis); };
  }, [layout, still, onSettled]);
  return <canvas ref={ref} role="img" aria-label="CreamFlow" className="absolute inset-0" />;
}

// ─── 3. Price trace ────────────────────────────────────────────────────────────
// A glowing gold line draws the letterforms left to right like a price trace, then the letters fill
// with cream; behind, a soft GEX-style heat glow pulses (rows of gold and lavender node glows) and a
// faint price line keeps drawing. The cursor warms the glows near it.
export function TraceTitle({ skip, still, onSettled }: ConceptProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const bgRef = useRef<HTMLCanvasElement>(null);
  const fonts = useFontsReady();
  const [geo, setGeo] = useState<{ W: number; H: number; size: number; letters: { ch: string; x: number; italic: boolean }[]; base: number } | null>(null);
  const [filled, setFilled] = useState(false);

  useEffect(() => {
    if (!fonts || !svgRef.current) return;
    const W = svgRef.current.parentElement!.clientWidth, H = svgRef.current.parentElement!.clientHeight;
    const size = Math.min(W * 0.15, 230);
    const ctx = document.createElement("canvas").getContext("2d")!;
    const letters: { ch: string; x: number; italic: boolean }[] = [];
    let x = 0;
    for (const [word, italic] of [["Cream", false], ["Flow", true]] as const) {
      ctx.font = `${italic ? "italic " : ""}400 ${size}px "Instrument Serif"`;
      for (const ch of word) { letters.push({ ch, x, italic }); x += ctx.measureText(ch).width; }
    }
    const x0 = (W - x) / 2;
    letters.forEach((l) => (l.x += x0));
    setGeo({ W, H, size, letters, base: H * 0.44 + size * 0.32 });
  }, [fonts]);

  const STAGGER = 0.12, DRAW = 1.35;
  const total = geo ? (geo.letters.length - 1) * STAGGER + DRAW : 0;
  useEffect(() => {
    if (!geo) return;
    if (still || skip) { setFilled(true); onSettled(); return; }
    const id = setTimeout(() => { setFilled(true); onSettled(); }, (total + 0.15) * 1000);
    return () => clearTimeout(id);
  }, [geo, still, skip, total, onSettled]);

  // background heat glow + a faint price line
  useEffect(() => {
    const canvas = bgRef.current;
    if (!canvas || !geo) return;
    const { W, H } = geo;
    const dpr = Math.min(2, devicePixelRatio || 1);
    canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.width = `${W}px`; canvas.style.height = `${H}px`;
    const ctx = canvas.getContext("2d")!;
    ctx.scale(dpr, dpr);
    const rows = [0.2, 0.3, 0.38, 0.47, 0.56, 0.66, 0.76];
    const blobs = rows.flatMap((ry, r) => Array.from({ length: 3 }, (_, k) => ({
      x: W * (0.12 + 0.76 * ((k + r * 0.37) % 3) / 2.4), y: H * ry, r: 40 + ((r * 7 + k * 13) % 5) * 22,
      pika: (r + k) % 3 !== 0, ph: r * 1.3 + k, king: r === 3 && k === 1,
    })));
    const price: number[] = [];
    let p = H * 0.55;
    for (let i = 0; i < 260; i++) { p += (Math.random() - 0.5) * 9; p = clamp(p, H * 0.25, H * 0.8); price.push(p); }
    let mx = -9999, my = -9999, raf = 0, off = 0, last = performance.now();
    const onMove = (e: PointerEvent) => { const r = canvas.getBoundingClientRect(); mx = e.clientX - r.left; my = e.clientY - r.top; };
    window.addEventListener("pointermove", onMove);
    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000); last = now;
      const t = now / 1000;
      ctx.clearRect(0, 0, W, H);
      for (const b of blobs) {
        const near = clamp(1 - Math.hypot(b.x - mx, b.y - my) / 260);
        const a = (b.king ? 0.34 : 0.13) * (0.75 + 0.25 * Math.sin(t * 0.8 + b.ph)) + near * 0.16;
        const col = b.pika ? "242,185,80" : "221,155,224";
        const g = ctx.createRadialGradient(b.x, b.y, 0, b.x, b.y, b.r * (b.king ? 1.6 : 1));
        g.addColorStop(0, `rgba(${col},${a})`); g.addColorStop(1, `rgba(${col},0)`);
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(b.x, b.y, b.r * (b.king ? 1.6 : 1), 0, Math.PI * 2); ctx.fill();
        if (b.king) { ctx.strokeStyle = `rgba(255,241,220,${0.18 + 0.08 * Math.sin(t * 1.2)})`; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(b.x, b.y, 16, 0, Math.PI * 2); ctx.stroke(); }
      }
      if (!still) { off += dt * 26; if (off > W / 259) { off = 0; price.shift(); let q = price[price.length - 1] + (Math.random() - 0.5) * 9; q = clamp(q, H * 0.25, H * 0.8); price.push(q); } }
      ctx.strokeStyle = "rgba(231,191,114,0.22)"; ctx.lineWidth = 1.2; ctx.beginPath();
      price.forEach((q, i) => { const x = (i / 259) * W - off; i ? ctx.lineTo(x, q) : ctx.moveTo(x, q); });
      ctx.stroke();
      if (!still) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    const onVis = () => { if (document.visibilityState === "hidden") cancelAnimationFrame(raf); else if (!still) { last = performance.now(); raf = requestAnimationFrame(frame); } };
    document.addEventListener("visibilitychange", onVis);
    return () => { cancelAnimationFrame(raf); window.removeEventListener("pointermove", onMove); document.removeEventListener("visibilitychange", onVis); };
  }, [geo, still]);

  const instant = still || skip;
  return (
    <>
      <canvas ref={bgRef} aria-hidden="true" className="absolute inset-0" />
      <svg ref={svgRef} role="img" aria-label="CreamFlow" className="absolute inset-0 h-full w-full" style={{ filter: "drop-shadow(0 0 6px rgba(231,191,114,.55))" }}>
        {geo && geo.letters.map((l, i) => (
          <text key={i} x={l.x} y={geo.base} fontFamily="Instrument Serif" fontStyle={l.italic ? "italic" : "normal"} fontSize={geo.size}
            fill={l.italic ? GOLD : CREAM} fillOpacity={filled ? 1 : 0} stroke={GOLD} strokeWidth={1.4} strokeOpacity={filled ? 0.25 : 1}
            style={{
              // the dash grows from nothing to the whole outline (never leaves a gap, whatever the glyph's length)
              strokeDasharray: instant ? "none" : `0 ${Math.round(geo.size * 6)}`,
              transition: "fill-opacity .6s cubic-bezier(.2,0,0,1), stroke-opacity .6s",
              animation: instant ? undefined : `trace-${Math.round(geo.size * 6)} ${DRAW}s cubic-bezier(.45,0,.25,1) ${i * STAGGER}s forwards`,
            }}>{l.ch}</text>
        ))}
        {geo && <style>{`@keyframes trace-${Math.round(geo.size * 6)} { to { stroke-dasharray: ${Math.round(geo.size * 6)} 0 } }`}</style>}
      </svg>
    </>
  );
}
