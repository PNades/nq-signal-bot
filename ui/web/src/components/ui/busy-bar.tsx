// Busy bar: after 21st.dev Busy 37649 (zweiundeins, MIT; code read 2026-11-16: no network calls, no remote assets; its
// `datastar-fetch` request watching is not taken). The pattern kept: a show delay so a quick load never flashes, a
// minimum time on screen once shown so it never flickers, a visually hidden status line for screen readers and the
// decoration aria-hidden. CreamFlow: a thin gold line that sweeps (Calm: a still line, no sweep), absolutely placed so
// it never moves the layout.
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export function BusyBar({ busy, delay = 300, min = 400, label = "Loading", animated, className, ...rest }: {
  busy: boolean; delay?: number; min?: number; label?: string; animated: boolean; className?: string;
} & Record<`data-${string}`, string | boolean | undefined>) {
  const [on, setOn] = useState(false);
  const shownAt = useRef(0);
  const sweep = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (busy) {
      if (on) return;
      const t = setTimeout(() => { shownAt.current = performance.now(); setOn(true); }, delay);
      return () => clearTimeout(t);
    }
    if (!on) return;
    const left = min - (performance.now() - shownAt.current);
    const t = setTimeout(() => setOn(false), Math.max(0, left));
    return () => clearTimeout(t);
  }, [busy, on, delay, min]);
  useEffect(() => {
    const el = sweep.current;
    if (!on || !animated || !el) return;
    const a = el.animate([{ transform: "translateX(-100%)" }, { transform: "translateX(250%)" }], { duration: 1100, iterations: Infinity, easing: "cubic-bezier(.4,0,.2,1)" });
    return () => a.cancel();
  }, [on, animated]);
  return (
    <div className={cn("pointer-events-none absolute inset-x-0 h-[2px] overflow-hidden rounded-full", className)} aria-hidden={false} {...rest} data-busy={on ? "true" : undefined}
      style={{ opacity: on ? 1 : 0, transition: animated ? "opacity 160ms ease-out" : undefined }}>
      <span className="sr-only" role="status">{on ? label : ""}</span>
      <span aria-hidden="true" className="absolute inset-0 bg-gold/15" />
      <span ref={sweep} aria-hidden="true" className={cn("absolute inset-y-0 left-0 w-2/5 bg-gold/70", !animated && "w-full bg-gold/35")} />
    </div>
  );
}
