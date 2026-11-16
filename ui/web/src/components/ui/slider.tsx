"use client";
// Slider with labelled marks: 21st.dev Marks Slider id 29217 (ai2), its LabelsSlider; code read 2026-11-15: no network
// calls, no package beyond motion. CreamFlow changes (pages/settings.md): a cap marker on the track (the risk $ slider
// is capped at drawdown ÷ N: the cap is drawn and the value can't pass it), the exact value shown by the caller in a
// mono number input beside it, the thumb grows 1.15× while dragged in Full only, an aria-label per slider.
import * as React from "react";
import { motion } from "motion/react";
import { cn } from "@/lib/utils";

const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

export function MarksSlider({ value, onChange, min, max, step, cap, marks = 5, format = (v) => String(Math.round(v)), label, animated = true, className }: {
  value: number; onChange: (v: number) => void; min: number; max: number; step: number; cap?: number; marks?: number;
  format?: (v: number) => string; label: string; animated?: boolean; className?: string;
}) {
  const track = React.useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = React.useState(false);
  const hi = cap !== undefined ? Math.min(cap, max) : max;
  const commit = (next: number) => onChange(clamp(Math.round(next / step) * step, min, hi));
  const fromX = (x: number) => {
    const r = track.current?.getBoundingClientRect();
    if (!r || r.width <= 0) return value;
    return min + clamp((x - r.left) / r.width, 0, 1) * (max - min);
  };
  const pct = ((clamp(value, min, max) - min) / (max - min)) * 100;
  const capPct = cap !== undefined ? ((clamp(cap, min, max) - min) / (max - min)) * 100 : null;
  return (
    <div className={cn("relative mb-6 flex w-full touch-none select-none items-center", className)}>
      <div ref={track} className="relative h-1.5 w-full cursor-pointer rounded-full bg-white/10"
        onPointerDown={(e) => { e.preventDefault(); track.current?.setPointerCapture?.(e.pointerId); setDragging(true); commit(fromX(e.clientX)); }}
        onPointerMove={(e) => { if (dragging) commit(fromX(e.clientX)); }}
        onPointerUp={(e) => { track.current?.releasePointerCapture?.(e.pointerId); setDragging(false); }}>
        <div className="absolute inset-y-0 left-0 rounded-full bg-cream" style={{ width: `${pct}%` }} />
        {capPct !== null && capPct < 100 && (
          <div className="absolute inset-y-0 right-0 rounded-r-full bg-[repeating-linear-gradient(135deg,rgba(255,255,255,.08)_0_3px,transparent_3px_6px)]" style={{ left: `${capPct}%` }} aria-hidden="true" />
        )}
        <div className="pointer-events-none absolute inset-0" aria-hidden="true">
          {Array.from({ length: marks }).map((_, i) => {
            const r = i / (marks - 1);
            return (
              <React.Fragment key={i}>
                <span className="absolute top-1/2 h-2 w-px -translate-x-1/2 -translate-y-1/2 bg-line-strong" style={{ left: `${r * 100}%` }} />
                <span className="absolute top-full mt-2 -translate-x-1/2 font-mono text-[11px] text-text-3" style={{ left: `${r * 100}%` }}>{format(min + r * (max - min))}</span>
              </React.Fragment>
            );
          })}
          {capPct !== null && (
            <span className="absolute -top-5 -translate-x-1/2 whitespace-nowrap font-mono text-[10.5px] text-warn" style={{ left: `${capPct}%` }}>cap {format(cap!)}</span>
          )}
        </div>
        <motion.button type="button" role="slider" aria-label={label} aria-valuenow={value} aria-valuemin={min} aria-valuemax={hi} aria-valuetext={format(value)}
          onPointerDown={(e) => { e.preventDefault(); e.stopPropagation(); track.current?.setPointerCapture?.(e.pointerId); setDragging(true); }}
          onKeyDown={(e) => {
            const d = e.shiftKey ? step * 10 : step;
            let n = value;
            if (e.key === "ArrowLeft" || e.key === "ArrowDown") n -= d; else if (e.key === "ArrowRight" || e.key === "ArrowUp") n += d;
            else if (e.key === "Home") n = min; else if (e.key === "End") n = hi; else return;
            e.preventDefault(); commit(n);
          }}
          className="absolute top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bg-0 bg-cream shadow-sm"
          style={{ left: `${pct}%` }} animate={{ scale: animated && dragging ? 1.15 : 1 }}
          transition={animated ? { type: "spring", stiffness: 500, damping: 30, mass: 0.6 } : { duration: 0 }} />
      </div>
    </div>
  );
}
