"use client";
// Burst: 21st.dev id 33152 (tinkerers-labs), code read 2026-11-15: no network calls. Its helper file
// (burst-utils/render-surface) was not returned by the registry, so the render loop is written here: the same
// physics, a canvas that runs only while pieces are alive, nothing in reduced motion / Calm (it announces only).
// Colours: CreamFlow cream, gold and ember (MASTER.md §3.3: ember is for celebration sparks).
import * as React from "react";
import { cn } from "@/lib/utils";

export type BurstHandle = { fire: (options?: { x?: number; y?: number; count?: number }) => void };
type Piece = { x: number; y: number; vx: number; vy: number; life: number; spin: number; angle: number; size: number; color: string };

export const Burst = React.forwardRef<BurstHandle, { colors?: string[]; count?: number; velocity?: number; gravity?: number; announce?: string; enabled?: boolean; className?: string }>(
  function Burst({ colors = ["#ffe0c2", "#f2b950", "#ff9a3c", "#eeeeee"], count = 70, velocity = 440, gravity = 900, announce, enabled = true, className }, ref) {
    const rootRef = React.useRef<HTMLDivElement>(null);
    const canvasRef = React.useRef<HTMLCanvasElement>(null);
    const pieces = React.useRef<Piece[]>([]);
    const raf = React.useRef(0);
    const [message, setMessage] = React.useState("");

    const run = React.useCallback(() => {
      const canvas = canvasRef.current, root = rootRef.current;
      if (!canvas || !root) return;
      const dpr = Math.min(2, devicePixelRatio || 1), r = root.getBoundingClientRect();
      canvas.width = r.width * dpr; canvas.height = r.height * dpr;
      const ctx = canvas.getContext("2d")!;
      let last = performance.now();
      const frame = (now: number) => {
        const dt = Math.min(0.05, (now - last) / 1000); last = now;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, r.width, r.height);
        const alive: Piece[] = [];
        for (const p of pieces.current) {
          p.life -= dt; if (p.life <= 0) continue;
          p.vy += gravity * dt; p.vx *= 1 - 1.2 * dt; p.x += p.vx * dt; p.y += p.vy * dt; p.angle += p.spin * dt;
          if (p.y - p.size > r.height) continue;
          alive.push(p);
          ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.angle); ctx.globalAlpha = Math.min(1, p.life * 1.6);
          ctx.fillStyle = p.color; ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2); ctx.restore();
        }
        pieces.current = alive;
        raf.current = alive.length ? requestAnimationFrame(frame) : 0;
        if (!alive.length) ctx.clearRect(0, 0, r.width, r.height);
      };
      cancelAnimationFrame(raf.current);
      raf.current = requestAnimationFrame(frame);
    }, [gravity]);

    React.useEffect(() => () => cancelAnimationFrame(raf.current), []);
    React.useImperativeHandle(ref, () => ({
      fire: (o) => {
        if (announce) setMessage(announce);
        if (!enabled || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        const r = rootRef.current!.getBoundingClientRect();
        const ox = o?.x ?? r.width / 2, oy = o?.y ?? r.height / 2;
        for (let i = 0; i < (o?.count ?? count); i++) {
          const a = Math.random() * Math.PI * 2, pw = velocity * (0.45 + Math.random() * 0.55);
          pieces.current.push({ x: ox, y: oy, vx: Math.cos(a) * pw, vy: Math.sin(a) * pw - velocity * 0.35, life: 0.9 + Math.random() * 0.8,
            spin: (Math.random() - 0.5) * 14, angle: Math.random() * Math.PI, size: 4 + Math.random() * 5, color: colors[i % colors.length] });
        }
        run();
      },
    }), [announce, count, velocity, colors, enabled, run]);

    return (
      <div ref={rootRef} className={cn("pointer-events-none absolute inset-0 z-50", className)}>
        <canvas ref={canvasRef} className="absolute inset-0 size-full" />
        <span className="sr-only" role="status" aria-live="polite">{message}</span>
      </div>
    );
  },
);

export default Burst;
