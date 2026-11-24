// Wheel, trackpad-scroll and pinch zoom for the main chart (owner 2026-11-24, UI polish round 2), in place of Lightweight
// Charts' own (`handleScale.mouseWheel` off). The library zooms each wheel event by 10% × min(1, |Δy| / 100) of the bar
// spacing: a mouse-wheel notch (Δy 100) ×1.10, but a trackpad's many small events (Δy 2-10 each) only ×1.03-1.05 a
// gesture, so the owner's trackpad took too many gestures. Here each event zooms by exp(step), with
//   step = ZOOM_CAP · tanh(|Δy| · ZOOM_SLOPE / ZOOM_CAP)   (+ = in when the wheel turns up / fingers pinch out):
// for small deltas it is linear at ZOOM_SLOPE, 2.5× the library's 0.001 per pixel, so a trackpad gesture zooms about
// 2.5× as far; a big delta saturates at ZOOM_CAP, so a mouse notch is ×1.13 (from ×1.10): not jumpy. Δy is normalised
// as the library does it (lines ×32, pages ×120; Chromium on Windows ÷ devicePixelRatio). The bar under the pointer
// stays under the pointer; the events of one frame are applied together (one redraw a frame, smooth). Horizontal
// scrolls stay the library's (pan). Zooming and panning only move the view: the chart holds the bars up to the replay
// clock and the overlay draws nothing past it (overlay.ts `end()`), so they never reveal anything later.
import type { IChartApi } from "lightweight-charts";

export const ZOOM_SLOPE = 0.0025;   // log zoom per wheel pixel, small deltas (the library: 0.001)
export const ZOOM_CAP = 0.13;       // the most one event zooms (log): a mouse notch ×1.13

const windowsChromium = typeof navigator !== "undefined" && /Windows/.test(navigator.userAgent) && /Chrome\//.test(navigator.userAgent);

/** the event's vertical delta in pixels, as the library normalises it */
export function wheelPixels(e: Pick<WheelEvent, "deltaY" | "deltaMode">, dpr = typeof window !== "undefined" ? window.devicePixelRatio : 1): number {
  if (e.deltaMode === 2) return e.deltaY * 120;
  if (e.deltaMode === 1) return e.deltaY * 32;
  return windowsChromium ? e.deltaY / (dpr || 1) : e.deltaY;
}

/** the log zoom of one event of `px` pixels (+ = in) */
export function zoomStep(px: number): number {
  return -Math.sign(px) * ZOOM_CAP * Math.tanh((Math.abs(px) * ZOOM_SLOPE) / ZOOM_CAP);
}

/** Zoom `chart` on wheel events over `el` (the plot) around the pointer; returns the detach function. */
export function attachWheelZoom(chart: IChartApi, el: HTMLElement): () => void {
  let pending = 0, px = 0, raf = 0;
  const apply = () => {
    raf = 0;
    const ts = chart.timeScale(), r = ts.getVisibleLogicalRange();
    const f = Math.exp(pending);
    pending = 0;
    if (!r || f === 1) return;
    const at = ts.coordinateToLogical(px) ?? (r.from + r.to) / 2;
    ts.setVisibleLogicalRange({ from: at - (at - r.from) / f, to: at + (r.to - at) / f });
  };
  const onWheel = (e: WheelEvent) => {
    if (e.deltaY === 0 || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;   // a horizontal scroll: the library pans
    if (e.cancelable) e.preventDefault();                                    // no page scroll, no browser zoom (pinch)
    pending += zoomStep(wheelPixels(e));
    px = e.clientX - el.getBoundingClientRect().left;
    if (!raf) raf = requestAnimationFrame(apply);
  };
  el.addEventListener("wheel", onWheel, { passive: false, capture: true });
  return () => { el.removeEventListener("wheel", onWheel, { capture: true }); if (raf) cancelAnimationFrame(raf); };
}
