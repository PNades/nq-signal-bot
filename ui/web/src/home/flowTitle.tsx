// Home's forming title, the order-flow concept the owner picked (2026-11-14; design-system/pages/home.md), ported from
// the step-14a mock (prototype of 21st.dev Pixel Flow Field 30145). It forms the CreamFlow title, calls onSettled once
// formed, idles and reacts to the cursor. `still` = Calm: the formed title drawn once, no loop. `skip` = formed at once.
// The loop stops while the tab is hidden (MASTER §8).
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { TitleLayout } from "./titleLayout";

/** `layout`: the shared title geometry (order flow uses it; re-laid out on every size change). */
export interface ConceptProps { skip: boolean; still: boolean; onSettled: () => void; layout?: TitleLayout | null }

const CREAM = "#F3E3C3";
const GOLD = "#E7BF72";
const clamp = (v: number, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

export function useFontsReady() {
  // already loaded (any visit after the first): ready in the very first frame (owner 2026-11-16: Home complete at once)
  const [ready, setReady] = useState(() => {
    try { return document.fonts.check('400 120px "Instrument Serif"', "CreamFlow") && document.fonts.check('italic 400 120px "Instrument Serif"', "Flow"); } catch { return false; }
  });
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
  // the faint background tape, kept across re-layouts (scaled to the new size) so a banner doesn't re-scatter it
  const tapeRef = useRef<{ W: number; H: number; dots: { x: number; y: number; v: number; s: number }[] } | null>(null);
  // set up before paint and drawn at once, so a re-layout never shows a cleared canvas for a frame (owner 2026-11-15)
  useLayoutEffect(() => {
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
    const prev = tapeRef.current;
    const tape = prev
      ? prev.dots.map((d) => ({ ...d, x: (d.x * W) / prev.W, y: (d.y * H) / prev.H }))
      : Array.from({ length: tapeN }, () => ({ x: Math.random() * W, y: Math.random() * H, v: 30 + Math.random() * 70, s: Math.random() < 0.5 ? 1 : -1 }));
    tapeRef.current = { W, H, dots: tape };
    let mx = -9999, my = -9999;
    const onMove = (e: PointerEvent) => { const r = canvas.getBoundingClientRect(); mx = e.clientX - r.left; my = e.clientY - r.top; };
    const onLeave = () => { mx = my = -9999; };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerleave", onLeave);
    const DUR = 1.05;
    // 60 fps in Full, also idle (owner 2026-11-15: an exception to the MASTER §8 CPU budget; Calm is the saving)
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
    draw(performance.now());
    const onVis = () => { if (document.visibilityState === "hidden") cancelAnimationFrame(raf); else if (!still) { last = performance.now(); raf = requestAnimationFrame(draw); } };
    document.addEventListener("visibilitychange", onVis);
    return () => { cancelAnimationFrame(raf); window.removeEventListener("pointermove", onMove); window.removeEventListener("pointerleave", onLeave); document.removeEventListener("visibilitychange", onVis); };
  }, [layout, still, onSettled]);
  return <canvas ref={ref} role="img" aria-label="CreamFlow" className="absolute inset-0" />;
}

// ─── 3. Price trace ────────────────────────────────────────────────────────────
// A glowing gold line draws the letterforms left to right like a price trace, then the letters fill
// with cream; behind, a soft GEX-style heat glow pulses (rows of gold and lavender node glows) and a
