// While the account dropdown or the command palette is open (hovered or pinned), the page behind it is inert (owner
// 2026-11-15): no hover effects and no click-through, only KILL and FLAT ALL stay live. Each open overlay adds its name
// to <html data-cf-overlay="...">; styles.css turns pointer events off on the app frame and the toasts and back on for
// [data-risk-live] and the open overlay's own trigger. An outside click then lands on the page root: the overlay closes
// and nothing underneath acts.
import { useEffect } from "react";

const open = new Set<string>();

function sync() {
  const root = document.documentElement;
  if (open.size) root.dataset.cfOverlay = [...open].join(" ");
  else delete root.dataset.cfOverlay;
}

export function useInertBehind(isOpen: boolean, name: string) {
  useEffect(() => {
    if (!isOpen) return;
    open.add(name); sync();
    return () => { open.delete(name); sync(); };
  }, [isOpen, name]);
}

/** a pointer event's target is one of the risk controls (KILL, FLAT ALL), which always act on the first click */
export const isRiskControl = (t: EventTarget | null) => t instanceof Element && t.closest("[data-risk-live]") !== null;
