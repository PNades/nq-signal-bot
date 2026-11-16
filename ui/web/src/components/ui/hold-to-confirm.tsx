"use client";
// Hold to Confirm: 21st.dev id 23527 (ddoemonn), code read 2026-11-15: React + motion only, no network calls.
// CreamFlow use (MASTER §10, pages/accounts.md): deleting an account or a saved preset only, never kill/flat. Changes:
// a 1.2-s hold by default; the sweep fills in crit red on the Cocoa surfaces; Calm (`animated` false) snaps the fill;
// the success face says what happened; it stays disabled until the caller says the typed name matches.
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { animate, motion, useMotionValue, useTransform } from "motion/react";
import { cn } from "@/lib/utils";

export type HoldPhase = "idle" | "holding" | "releasing" | "committed";

function useHoldToConfirm({ onConfirm, duration, releaseRate = 2.5, disabled }: { onConfirm: () => void; duration: number; releaseRate?: number; disabled: boolean }) {
  const [phase, setPhase] = useState<HoldPhase>("idle");
  const phaseRef = useRef<HoldPhase>("idle");
  const down = useRef(false);
  const elapsed = useRef(0);
  const last = useRef(0);
  const raf = useRef(0);
  const confirm = useRef(onConfirm);
  confirm.current = onConfirm;
  const move = useCallback((next: HoldPhase) => { phaseRef.current = next; setPhase(next); }, []);
  const begin = useCallback(() => {
    if (disabled || phaseRef.current === "committed" || phaseRef.current === "holding") return;
    down.current = true;
    move("holding");
    if (raf.current) return;
    last.current = performance.now();
    const loop = (now: number) => {
      const dt = Math.min(64, now - last.current);
      last.current = now;
      elapsed.current += down.current ? dt : -dt * releaseRate;
      if (elapsed.current >= duration) { raf.current = 0; elapsed.current = duration; down.current = false; move("committed"); confirm.current(); return; }
      if (elapsed.current <= 0) { raf.current = 0; elapsed.current = 0; move("idle"); return; }
      raf.current = requestAnimationFrame(loop);
    };
    raf.current = requestAnimationFrame(loop);
  }, [disabled, duration, releaseRate, move]);
  const release = useCallback(() => {
    if (phaseRef.current !== "holding") return;
    down.current = false;
    move("releasing");
  }, [move]);
  useEffect(() => () => cancelAnimationFrame(raf.current), []);
  const bind = {
    onPointerDown: (e: React.PointerEvent) => { if (e.pointerType === "mouse" && e.button !== 0) return; e.currentTarget.setPointerCapture?.(e.pointerId); begin(); },
    onPointerUp: release, onPointerCancel: release, onPointerLeave: release,
    onKeyDown: (e: React.KeyboardEvent) => { if (e.repeat) return; if (e.key === " " || e.key === "Enter") { e.preventDefault(); begin(); } },
    onKeyUp: (e: React.KeyboardEvent) => { if (e.key === " " || e.key === "Enter") release(); },
    onBlur: release,
    onClick: (e: React.MouseEvent) => e.preventDefault(),
    onContextMenu: (e: React.MouseEvent) => e.preventDefault(),
  };
  return { bind, phase };
}

export function HoldToConfirm({ onConfirm, children, confirmLabel = "Deleted", duration = 1200, disabled = false, animated = true, className }: {
  onConfirm: () => void; children: React.ReactNode; confirmLabel?: string; duration?: number; disabled?: boolean; animated?: boolean; className?: string;
}) {
  const { bind, phase } = useHoldToConfirm({ onConfirm, duration, disabled });
  const hintId = useId();
  const swept = useMotionValue(0);
  const clipPath = useTransform(swept, (v) => `inset(0 ${(1 - v) * 100}% 0 0)`);
  useEffect(() => {
    if (!animated) { swept.set(phase === "holding" || phase === "committed" ? 1 : 0); return; }
    const from = swept.get();
    const c = phase === "holding" ? animate(swept, 1, { duration: (duration * (1 - from)) / 1000, ease: "linear" })
      : phase === "committed" ? animate(swept, 1, { duration: 0.12 })
      : animate(swept, 0, { duration: (duration * from) / 2.5 / 1000, ease: [0.23, 1, 0.32, 1] });
    return () => c.stop();
  }, [phase, duration, animated, swept]);
  const face = phase === "committed" ? confirmLabel : children;
  return (
    <button type="button" aria-disabled={disabled} aria-describedby={hintId} {...bind}
      className={cn("relative isolate inline-grid h-9 select-none place-items-center overflow-hidden rounded-sm border border-crit/60 bg-transparent px-4 text-[13px] font-medium text-crit",
        disabled ? "cursor-not-allowed opacity-45" : "cursor-pointer hover:bg-crit/10", className)}>
      <span className="col-start-1 row-start-1 flex items-center gap-2 whitespace-nowrap">{face}</span>
      <motion.span aria-hidden style={{ clipPath }} className="absolute inset-0 grid place-items-center bg-crit-fill px-4 text-white">
        <span className="flex items-center gap-2 whitespace-nowrap">{face}</span>
      </motion.span>
      <span id={hintId} className="sr-only">Press and hold for {(duration / 1000).toFixed(1)} seconds to confirm. Releasing early cancels.</span>
    </button>
  );
}
