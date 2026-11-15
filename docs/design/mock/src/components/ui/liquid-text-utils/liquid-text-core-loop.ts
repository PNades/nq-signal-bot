// Runs `frame(dt)` on requestAnimationFrame only while the element is on screen and the tab is visible
// (MASTER §8 pause rules). Local rewrite of the helper the component imports.
import { useEffect, useRef, type RefObject } from "react";

export function useVisibleLoop(ref: RefObject<HTMLElement | null>, frame: (dt: number) => void, opts: { enabled: boolean }) {
  const frameRef = useRef(frame);
  frameRef.current = frame;
  useEffect(() => {
    const el = ref.current;
    if (!el || !opts.enabled) return;
    let raf = 0, last = 0, onScreen = true;
    const tick = (t: number) => {
      const dt = last ? Math.min(0.05, (t - last) / 1000) : 1 / 60;
      last = t;
      frameRef.current(dt);
      raf = requestAnimationFrame(tick);
    };
    const start = () => { if (!raf && onScreen && document.visibilityState === "visible") { last = 0; raf = requestAnimationFrame(tick); } };
    const stop = () => { if (raf) cancelAnimationFrame(raf); raf = 0; };
    const io = new IntersectionObserver(([e]) => { onScreen = e.isIntersecting; onScreen ? start() : stop(); });
    io.observe(el);
    const onVis = () => (document.visibilityState === "visible" ? start() : stop());
    document.addEventListener("visibilitychange", onVis);
    start();
    return () => { stop(); io.disconnect(); document.removeEventListener("visibilitychange", onVis); };
  }, [ref, opts.enabled]);
}
