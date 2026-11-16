// Hints: CreamFlow's own styled tooltip, in place of the browser's native title tooltips (none anywhere in the panel,
// owner 2026-11-15). Look from originui Tooltip (21st.dev id 207, the dark variant; code read 2026-11-15: Radix only,
// no network calls, no embedded instructions): a small dark popover with a hairline border, fading and zooming in from
// 95%. Instead of wrapping every trigger in Radix providers, one delegated layer shows the `data-hint` text of the
// element under the pointer (after HINT_DELAY_MS) or of a keyboard-focused one, below it (above when there is no room),
// kept inside the window. Hidden on any press, key, scroll or when the pointer leaves.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { useAnimated } from "@/effects";

const HINT_DELAY_MS = 450, GAP = 6, EDGE = 8;

export function HintLayer() {
  const animated = useAnimated();
  const [hint, setHint] = useState<{ text: string; rect: DOMRect } | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const tip = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let timer = 0, current: Element | null = null;
    const find = (t: EventTarget | null) => (t instanceof Element ? t.closest("[data-hint]") : null);
    const later = (el: Element) => {
      clearTimeout(timer);
      timer = window.setTimeout(() => { const text = el.getAttribute("data-hint"); if (text && el.isConnected) setHint({ text, rect: el.getBoundingClientRect() }); }, HINT_DELAY_MS);
    };
    const hide = () => { clearTimeout(timer); current = null; setHint(null); };
    const over = (e: PointerEvent) => { const el = find(e.target); if (el === current) return; hide(); current = el; if (el) later(el); };
    const out = (e: PointerEvent) => { if (!e.relatedTarget) hide(); };
    const focus = (e: FocusEvent) => { const t = e.target as Element; const el = find(t); if (el && t.matches?.(":focus-visible")) { hide(); current = el; later(el); } };
    document.addEventListener("pointerover", over);
    document.addEventListener("pointerout", out);
    document.addEventListener("pointerdown", hide, true);
    document.addEventListener("keydown", hide, true);
    document.addEventListener("focusin", focus);
    document.addEventListener("focusout", hide);
    window.addEventListener("scroll", hide, true);
    window.addEventListener("blur", hide);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("pointerover", over);
      document.removeEventListener("pointerout", out);
      document.removeEventListener("pointerdown", hide, true);
      document.removeEventListener("keydown", hide, true);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("focusout", hide);
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("blur", hide);
    };
  }, []);
  useLayoutEffect(() => {
    if (!hint || !tip.current) { setPos(null); return; }
    const w = tip.current.offsetWidth, h = tip.current.offsetHeight, r = hint.rect;
    const below = r.bottom + GAP + h <= window.innerHeight - EDGE;
    setPos({
      left: Math.max(EDGE, Math.min(window.innerWidth - w - EDGE, r.left + r.width / 2 - w / 2)),
      top: below ? r.bottom + GAP : Math.max(EDGE, r.top - GAP - h),
    });
  }, [hint]);
  return createPortal(
    <AnimatePresence>
      {hint && (
        <motion.div ref={tip} key={hint.text + hint.rect.left} role="tooltip" data-hint-tip
          className="pointer-events-none fixed z-[70] max-w-[280px] rounded-lg border border-line-strong bg-bg-3 px-2 py-1 text-[12px] leading-4 text-text-1 shadow-[0_8px_24px_rgba(0,0,0,.5)]"
          style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999, visibility: pos ? "visible" : "hidden" }}
          initial={animated ? { opacity: 0, scale: 0.95 } : { opacity: 1 }} animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, transition: { duration: animated ? 0.08 : 0 } }} transition={{ duration: animated ? 0.12 : 0 }}>
          {hint.text}
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
