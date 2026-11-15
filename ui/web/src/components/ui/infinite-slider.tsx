// InfiniteSlider: from 21st.dev Logo Marquee id 18221 (grootstudio), code read 2026-11-15: one motion value, a linear
// loop, no network calls (the demo's logos come from a CDN and are not used). CreamFlow changes: react-use-measure
// replaced by a ResizeObserver; `running` false = Calm (a static row, no loop); the loop also stops while the tab is
// hidden (MASTER §8 pause rules); hover slows it (durationOnHover).
import { memo, useEffect, useRef, useState } from "react";
import { animate, motion, useMotionValue } from "motion/react";
import { cn } from "@/lib/utils";
import { usePageVisible } from "@/lib/hooks";

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.offsetWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export const InfiniteSlider = memo(function InfiniteSlider({ children, gap = 32, duration = 60, durationOnHover, running = true, className }: {
  children: React.ReactNode; gap?: number; duration?: number; durationOnHover?: number; running?: boolean; className?: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const x = useMotionValue(0);
  const visible = usePageVisible();
  const [hover, setHover] = useState(false);
  const loop = running && visible && width > 0;
  useEffect(() => {
    if (!loop) { if (!running) x.set(0); return; }
    const to = -(width + gap) / 2;
    const d = hover && durationOnHover ? durationOnHover : duration;
    const now = x.get();
    const from = now <= to || now > 0 ? 0 : now;
    let rest: ReturnType<typeof animate> | undefined;
    let stopped = false;
    // finish the current lap at the current speed, then loop from the start
    const first = animate(x, [from, to], { ease: "linear", duration: d * ((to - from) / to) });
    first.then(() => { if (!stopped) rest = animate(x, [0, to], { ease: "linear", duration: d, repeat: Infinity, repeatType: "loop" }); });
    return () => { stopped = true; first.stop(); rest?.stop(); };
  }, [loop, running, width, gap, duration, durationOnHover, hover, x]);
  return (
    <div className={cn("overflow-hidden", className)} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <motion.div ref={ref} className="flex w-max" style={{ x, gap: `${gap}px` }}>
        {children}
        {running && children}
      </motion.div>
    </div>
  );
});
