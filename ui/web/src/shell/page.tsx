// Shared page parts (pages/shell.md): the page header (eyebrow, two-line title with the second line in cream italic,
// subtitle), the empty state (Dot Matrix Text, a one-line message, the next action; never urgent information) and
// skeletons shaped like the content.
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { DotMatrixText } from "@/components/ui/dot-text";
import HalftoneWordmarkFooter from "@/components/ui/halftone-wordmark-footer";
import { useAnimated } from "@/effects";
import { cn } from "@/lib/utils";
import type { PageDef } from "./pages";

export function PageHeader({ page, right, children }: { page: PageDef; right?: ReactNode; children?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <div className="eyebrow"><span className="text-text-2">CREAMFLOW</span> / {page.name.toUpperCase()}</div>
        <h1 className="mt-2 font-display text-[38px] leading-[40px] tracking-[-0.01em] text-text-1">
          {page.title[0]} <span className="italic text-cream">{page.title[1]}</span>
        </h1>
        {page.subtitle && <p className="mt-1.5 text-[16px] leading-6 text-text-2">{page.subtitle}</p>}
        {children}
      </div>
      {right && <div className="flex shrink-0 flex-wrap items-center gap-2">{right}</div>}
    </header>
  );
}

export function EmptyState({ word, message, action, className }: { word: string; message: ReactNode; action?: ReactNode; className?: string }) {
  const animated = useAnimated();
  return (
    <div className={cn("flex flex-col items-center gap-4 rounded-md border border-line-strong/60 bg-bg-1 px-6 py-10 text-center", className)} data-empty-state>
      <div className="relative h-[72px] w-[min(100%,420px)] text-cream" aria-hidden="true">
        <DotMatrixText text={word} transition={animated ? "fade" : "none"} dotSize={3} gap={2} activeColor="#FFE0C2" fontFamily='"Geist Variable", system-ui, sans-serif' className="absolute inset-0" />
      </div>
      <p className="max-w-[56ch] text-[14px] leading-5 text-text-2">{message}</p>
      {action}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("cf-skeleton", className)} aria-hidden="true" />;
}

// ── the dot-matrix footer that ends the scrolling pages (Accounts, Settings, Performance; picks.md) ─────────────────

// the footer word's weight from the footer's width: SemiBold (600) at the 960-px window (≈ 945 px of footer), Black (900)
// at full screen (≈ 1905 px), straight in between (owner 2026-11-15)
const FOOTER_W_NARROW = 945, FOOTER_W_FULL = 1905, FOOTER_WEIGHT_NARROW = 600, FOOTER_WEIGHT_FULL = 900;
function footerWeight(width: number) {
  const t = Math.min(1, Math.max(0, (width - FOOTER_W_NARROW) / (FOOTER_W_FULL - FOOTER_W_NARROW)));
  return Math.round((FOOTER_WEIGHT_NARROW + t * (FOOTER_WEIGHT_FULL - FOOTER_WEIGHT_NARROW)) / 10) * 10;
}

/** A scrolling page that ends inside the "cream" letters (2/7 of their height hidden by the bottom edge): it cancels
 *  the bottom padding the dock and date block keep (--dock-pad), which may float over the letters.
 *  `ready` (owner 2026-11-15): until the page's content is in place, the content area fills at least the window and the
 *  footer isn't rendered, so it can never flash into view while a page loads; it then fades in (Calm: at once). */
export function FooterPage({ children, ready = true }: { children: ReactNode; ready?: boolean }) {
  const animated = useAnimated();
  return (
    <div className="flex min-h-full flex-col" style={{ marginBottom: "calc(-1 * var(--dock-pad, 0px))" }} data-footer-page data-ready={ready ? "1" : "0"}>
      <div className="flex-1" style={ready ? undefined : { minHeight: "100dvh" }}>{children}</div>
      {ready && (
        <motion.div initial={animated ? { opacity: 0 } : false} animate={{ opacity: 1 }} transition={{ duration: animated ? 0.24 : 0, ease: [0, 0, 0.2, 1] }}
          // the same gap above the footer on every scrolling page (owner 2026-11-16: 80 px from the content to its label)
          className="mt-10 shrink-0" data-page-footer>
          <HalftoneWordmarkFooter
            wordmark="cream" label="CreamFlow · NQ" meta="Local control panel · 127.0.0.1" columns={[]} backToTop={false}
            palette={{ background: "#111111", ink: "#ffe0c2", warm: "#ff9a3c" }} fontFamily='"Geist Variable", ui-sans-serif, sans-serif'
            fontWeight={footerWeight} fit="inset" fitHeight={{ gapRatio: 0.18, minGap: 32 }}
            monoFamily='"Geist Mono Variable", monospace' crop={2 / 7} grain={0.25} interactive={animated} height="320px"
            className="shrink-0" />
        </motion.div>
      )}
    </div>
  );
}

// The fold's motion (shell/Banners.tsx): ~250 ms, ease-out
const FOLD_S = 0.25;
const FOLD_EASE = [0, 0, 0.2, 1] as const;

/**
 * A block whose height follows its content smoothly (owner 2026-11-16): when what is inside gets shorter or taller
 * (a Performance tab switch), the height animates from the old size to the new one, so what is below (the footer)
 * glides into place with a slight fade instead of snapping. Only this block's height changes (its content keeps its own
 * size, so charts never resize); overflow is clipped only while it moves. Calm: at once.
 */
export function AutoHeight({ children, className }: { children: ReactNode; className?: string }) {
  const animated = useAnimated();
  const inner = useRef<HTMLDivElement>(null);
  const [h, setH] = useState<number | null>(null);
  const [moving, setMoving] = useState(false);
  const last = useRef<number | null>(null);
  useLayoutEffect(() => {
    const el = inner.current;
    if (!el) return;
    const measure = () => {
      const next = el.offsetHeight;
      const prev = last.current;
      last.current = next;
      setH(next);
      if (prev === null || !animated || Math.abs(next - prev) < 8) return;
      // the footer fades a little while it glides: in when it comes up into view, out when it goes down
      const foot = document.querySelector("[data-page-footer]") as HTMLElement | null;
      foot?.animate({ opacity: next < prev ? [0.35, 1] : [1, 0.35, 1] }, { duration: FOLD_S * 1000, easing: "cubic-bezier(0, 0, 0.2, 1)" });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [animated]);
  return (
    // Calm: no set height at all, so a new size shows in the same frame as the new content
    <motion.div className={className} data-auto-height data-moving={moving ? "1" : "0"}
      initial={false} animate={h === null || !animated ? undefined : { height: h }} transition={{ duration: animated ? FOLD_S : 0, ease: FOLD_EASE }}
      onAnimationStart={() => setMoving(true)} onAnimationComplete={() => setMoving(false)}
      style={{ overflow: moving && animated ? "hidden" : "visible", height: animated ? undefined : "auto" }}>
      <div ref={inner} className="flow-root">{children}</div>
    </motion.div>
  );
}

/**
 * A section that comes and goes with the same height glide as AutoHeight (owner 2026-11-16: the Performance page's
 * "All accounts" breakdown on an account switch): it opens from no height while it fades in, and closes the same way, so
 * what is below moves smoothly instead of jumping. Calm: at once.
 */
export function Reveal({ show, children, ...rest }: { show: boolean; children: ReactNode } & Record<`data-${string}`, string | undefined>) {
  const animated = useAnimated();
  const t = { duration: FOLD_S, ease: FOLD_EASE };
  // Calm: the section is simply there or not, in the same frame as the rest
  if (!animated) return show ? <div {...rest}><div className="flow-root">{children}</div></div> : null;
  return (
    <AnimatePresence initial={false}>
      {show && (
        <motion.div key="reveal" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
          transition={t} style={{ overflow: "hidden" }} {...rest}>
          <div className="flow-root">{children}</div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
