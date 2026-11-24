// InfoTip: the ⓘ icon and its info box (owner 2026-11-24, UI polish round 2), used for every info icon in the panel
// (`ChartHeader`). The pointer opens it on hover; moving off both the icon and the box closes it (a short grace lets the
// pointer cross the gap between them). A click pins it open: it stays without the pointer until a click anywhere
// outside the box, Esc, or another click on the icon. The icon is a real button: a mouse click draws no focus ring, the
// keyboard keeps the cream ring (styles.css, keyboard only), Tab onto it opens the box, Enter / Space pins it. The box
// uses the hint tooltip's look (hint-layer.tsx), wider for longer text, kept inside the window.
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { Info } from "lucide-react";
import { useAnimated } from "@/effects";
import { cn } from "@/lib/utils";

const GAP = 6, EDGE = 8;
export const INFO_CLOSE_GRACE_MS = 140;   // icon → box: the pointer crosses the gap without closing it

type Mode = "hover" | "focus" | "pinned";

export function InfoTip({ text, className, iconClassName }: { text: string; className?: string; iconClassName?: string }) {
  const animated = useAnimated();
  const id = useId();
  const [mode, setMode] = useState<Mode | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const timer = useRef(0);
  const modeRef = useRef(mode);
  modeRef.current = mode;

  const cancelClose = () => { window.clearTimeout(timer.current); };
  const closeSoon = () => {
    cancelClose();
    timer.current = window.setTimeout(() => { if (modeRef.current === "hover") setMode(null); }, INFO_CLOSE_GRACE_MS);
  };
  const close = useCallback(() => { cancelClose(); setMode(null); }, []);

  // pinned: a click outside the icon and the box, or Esc, closes it
  useEffect(() => {
    if (!mode) return;
    const down = (e: PointerEvent) => {
      const t = e.target as Node;
      if (btn.current?.contains(t) || box.current?.contains(t)) return;
      close();
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
    const away = () => { if (modeRef.current !== "pinned") close(); };
    document.addEventListener("pointerdown", down, true);
    document.addEventListener("keydown", key, true);
    window.addEventListener("blur", away);
    return () => { document.removeEventListener("pointerdown", down, true); document.removeEventListener("keydown", key, true); window.removeEventListener("blur", away); };
  }, [mode, close]);
  useEffect(() => () => cancelClose(), []);

  // below the icon (above when there is no room), inside the window; follows the icon while the page scrolls
  const place = useCallback(() => {
    const b = btn.current, el = box.current;
    if (!b || !el) return;
    const r = b.getBoundingClientRect(), w = el.offsetWidth, h = el.offsetHeight;
    const below = r.bottom + GAP + h <= window.innerHeight - EDGE;
    setPos({ left: Math.max(EDGE, Math.min(window.innerWidth - w - EDGE, r.left + r.width / 2 - w / 2)),
      top: below ? r.bottom + GAP : Math.max(EDGE, r.top - GAP - h) });
  }, []);
  useLayoutEffect(() => { if (mode) place(); else setPos(null); }, [mode, place]);
  useEffect(() => {
    if (!mode) return;
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => { window.removeEventListener("scroll", place, true); window.removeEventListener("resize", place); };
  }, [mode, place]);

  const isMouse = (e: React.PointerEvent) => e.pointerType === "mouse" || e.pointerType === "pen";
  return (
    <>
      <button ref={btn} type="button" aria-label="More information" aria-expanded={mode !== null} aria-describedby={mode ? id : undefined}
        data-info data-info-state={mode ?? "closed"}
        className={cn("inline-flex size-4 shrink-0 items-center justify-center rounded-full text-text-3 transition-colors hover:text-text-1 focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cream", mode && "text-text-1", className)}
        onPointerEnter={(e) => { if (!isMouse(e)) return; cancelClose(); setMode((m) => m ?? "hover"); }}
        onPointerLeave={(e) => { if (isMouse(e) && modeRef.current === "hover") closeSoon(); }}
        onFocus={(e) => { if (e.currentTarget.matches(":focus-visible")) setMode((m) => m ?? "focus"); }}
        onBlur={() => { if (modeRef.current === "focus") setMode(null); }}
        onClick={() => { cancelClose(); setMode((m) => (m === "pinned" ? null : "pinned")); }}>
        <Info className={cn("size-3", iconClassName)} aria-hidden="true" />
      </button>
      {createPortal(
        <AnimatePresence>
          {mode && (
            <motion.div ref={box} id={id} role="tooltip" data-info-box data-pinned={mode === "pinned" ? "" : undefined}
              onPointerEnter={cancelClose} onPointerLeave={() => { if (modeRef.current === "hover") closeSoon(); }}
              className="fixed z-[70] max-w-[320px] rounded-lg border border-line-strong bg-bg-3 px-3 py-2 text-[12px] leading-[18px] text-text-1 shadow-[0_8px_24px_rgba(0,0,0,.5)]"
              style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999, visibility: pos ? "visible" : "hidden" }}
              initial={animated ? { opacity: 0, scale: 0.97 } : { opacity: 1 }} animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, transition: { duration: animated ? 0.08 : 0 } }} transition={{ duration: animated ? 0.12 : 0 }}>
              {text}
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
