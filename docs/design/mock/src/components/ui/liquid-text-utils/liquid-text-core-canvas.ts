// Helper for Liquid Text (isaiahbjork, 21st.dev id 43190). get_component didn't return the helpers,
// so this is a local rewrite of the three functions the component imports.
import { useEffect, useState, type RefObject } from "react";

export function defaultMaxDpr(): number { return 2; } // MASTER §8: device pixel ratio capped at 2

export function sizeCanvas(canvas: HTMLCanvasElement, w: number, h: number, maxDpr: number): number {
  const dpr = Math.min(maxDpr, typeof window === "undefined" ? 1 : window.devicePixelRatio || 1);
  canvas.width = Math.max(1, Math.round(w * dpr));
  canvas.height = Math.max(1, Math.round(h * dpr));
  canvas.style.width = `${w}px`;
  canvas.style.height = `${h}px`;
  return dpr;
}

export function useElementSize(ref: RefObject<HTMLElement | null>) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const r = e.contentRect;
      setSize((s) => (s.width === Math.round(r.width) && s.height === Math.round(r.height) ? s : { width: Math.round(r.width), height: Math.round(r.height) }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}
