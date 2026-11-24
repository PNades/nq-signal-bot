// Shared page parts (pages/shell.md): the page header (eyebrow, two-line title with the second line in cream italic,
// subtitle), the empty state (Dot Matrix Text, a one-line message, the next action; never urgent information) and
// skeletons shaped like the content.
import { useEffect, useInsertionEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
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

// the footer word's weight: Black (900), the full-screen weight, at every width; only its size follows the footer's width
// (owner 2026-11-24, round 3; it was tied to the width, 600 at the 960-px window up to 900 at full screen, owner
// 2026-11-15, so the letters thinned as the window narrowed: 600 at 960, 700 at 1280, 750 at 1440)
const FOOTER_WEIGHT = 900;

/** A scrolling page's content. Owner 2026-11-16: the footer is no longer the page's own: every scrolling page shares
 *  the one in PagesFrame (main.tsx), which stays in place across page changes and glides with the height. */
export function FooterPage({ children }: { children: ReactNode; ready?: boolean }) {
  return <>{children}</>;
}

/**
 * The height glide on a page change (owner 2026-11-16): when `k` changes (the next page has come in, fully filled), the
 * block animates from the old page's height to the new one's (the tab switch's glide), so the footer below glides into
 * place; between page changes it follows its content as it is. Calm: at once.
 */
export function GlideOnKey({ k, children }: { k: string; children: ReactNode }) {
  const animated = useAnimated();
  const box = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const last = useRef<number | null>(null);
  const prevK = useRef(k);
  // the glide in progress: from, to, progress (ms) and length (ms); driven frame by frame, each frame counting at most
  // GLIDE_STEP_MS, so a frame that comes late (a heavy page painting) never carries most of the move
  const run = useRef<{ from: number; to: number; t: number; ms: number; raf: number } | null>(null);
  // the page that leaves keeps its scroll (owner 2026-11-16): the next page is moved down by the scroll so it opens at
  // the top of the window; when the glide ends (the leaving page is gone by then) the move and the scroll go back to 0
  // together, so nothing moves on screen
  const shift = useRef<{ el: HTMLElement; s: number } | null>(null);
  const unshift = () => {
    const sh = shift.current;
    shift.current = null;
    if (!sh) return;
    sh.el.style.transform = "";
    const main = document.getElementById("cf-main");
    if (main) main.scrollTop = Math.max(0, main.scrollTop - sh.s);
  };
  const stop = () => {
    const b = box.current;
    if (run.current) cancelAnimationFrame(run.current.raf);
    run.current = null;
    if (b) { b.style.height = ""; b.style.overflow = ""; }
    unshift();
    last.current = inner.current?.offsetHeight ?? last.current;
  };
  const start = (b: HTMLDivElement, from: number, to: number, ms: number) => {
    if (run.current) cancelAnimationFrame(run.current.raf);
    b.style.overflow = "hidden";
    b.style.height = `${from}px`;
    const r = { from, to, t: 0, ms, raf: 0 };
    run.current = r;
    let prev = performance.now();
    const tick = (now: number) => {
      if (run.current !== r) return;
      r.t += Math.min(Math.max(0, now - prev), GLIDE_STEP_MS);
      prev = now;
      const p = Math.min(1, r.t / r.ms);
      b.style.height = `${r.from + (r.to - r.from) * foldCurve(p)}px`;
      if (p < 1) r.raf = requestAnimationFrame(tick); else stop();
    };
    r.raf = requestAnimationFrame(tick);
  };
  useLayoutEffect(() => {
    const el = inner.current;
    if (!el) return;
    last.current = el.offsetHeight;
    // while it glides, the next page can still settle (a few pixels as it lays out): the glide follows the live height
    // to its end instead of snapping at the end
    const ro = new ResizeObserver(() => {
      const r = run.current, b = box.current;
      if (!r || !b) { last.current = el.offsetHeight; return; }
      const to = el.offsetHeight + (shift.current?.s ?? 0);
      if (Math.abs(to - r.to) < 1) return;
      r.from = b.getBoundingClientRect().height; r.to = to; r.ms = Math.max(80, r.ms - r.t); r.t = 0;
    });
    ro.observe(el);
    return () => { ro.disconnect(); if (run.current) cancelAnimationFrame(run.current.raf); };
  }, []);
  // the same hold, earlier: insertion effects run before every layout effect (the next page's own may read the layout)
  const heldFor = useRef(k);
  useInsertionEffect(() => {
    if (heldFor.current === k) return;
    heldFor.current = k;
    const b = box.current;
    if (b && !run.current && last.current !== null) b.style.height = `${last.current}px`;
  }, [k]);
  useLayoutEffect(() => {
    if (prevK.current === k) return;
    prevK.current = k;
    const b = box.current, el = inner.current;
    if (!b || !el) return;
    // hold the old height before anything reads the layout: with the leaving page taken out of the flow the content is
    // shorter, and the browser would clamp the scroll of the page that's leaving at the first layout
    if (!run.current && last.current !== null) b.style.height = `${last.current}px`;
    const main = document.getElementById("cf-main");
    const views = [...el.querySelectorAll<HTMLElement>(":scope > [data-view]")];
    if (!animated) {
      // Calm: an instant swap: the leaving page is hidden in this very frame and the next one is at its top
      for (const v of views) if (v.getAttribute("data-view") !== k) v.style.visibility = "hidden";
      stop();
      if (main) main.scrollTop = 0;
      return;
    }
    const s = main?.scrollTop ?? 0;
    const fresh = views.find((v) => v.getAttribute("data-view") === k)?.querySelector<HTMLElement>(":scope > [data-shift]") ?? null;
    // a change during a change: the page that was coming in is now the one leaving (it keeps its move until it's gone)
    shift.current = null;
    if (fresh && s > 0) { fresh.style.transform = `translateY(${s}px)`; shift.current = { el: fresh, s }; }
    const from = run.current ? b.getBoundingClientRect().height : last.current, to = el.offsetHeight + (shift.current ? s : 0);
    if (from === null || (Math.abs(to - from) < 8 && !shift.current)) { stop(); return; }
    start(b, from, to, FOLD_S * 1000);
  }, [k, animated]); // eslint-disable-line react-hooks/exhaustive-deps
  return <div ref={box} className="relative" data-glide-box><div ref={inner} className="flow-root">{children}</div></div>;
}

/** The scrolling pages' frame: the page area (its height glides on a page change) and the one dot-matrix footer, which
 *  ends inside the "cream" letters (2/7 of their height hidden by the bottom edge) by cancelling the bottom padding the
 *  dock and date block keep (--dock-pad). */
export function PagesFrame({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex min-h-full flex-col" style={{ marginBottom: "calc(-1 * var(--dock-pad, 0px))" }} data-footer-page>
      <div className="flex-1"><GlideOnKey k={k}>{children}</GlideOnKey></div>
      <PageFooter />
    </div>
  );
}

function PageFooter() {
  const animated = useAnimated();
  return (
        <div
          // the same gap above the footer on every scrolling page (owner 2026-11-16: 80 px from the content to its label)
          className="mt-10 shrink-0" data-page-footer>
          <HalftoneWordmarkFooter
            wordmark="cream" label="CreamFlow · NQ" meta="Local control panel · 127.0.0.1" columns={[]} backToTop={false}
            palette={{ background: "#111111", ink: "#ffe0c2", warm: "#ff9a3c" }} fontFamily='"Geist Variable", ui-sans-serif, sans-serif'
            fontWeight={FOOTER_WEIGHT} fit="inset" fitHeight={{ gapRatio: 0.18, minGap: 32 }}
            monoFamily='"Geist Mono Variable", monospace' crop={2 / 7} grain={0.25} interactive={animated} height="320px"
            className="shrink-0" />
        </div>
  );
}

// The fold's motion (shell/Banners.tsx): ~250 ms, ease-out
const FOLD_S = 0.25;
const FOLD_EASE = [0, 0, 0.2, 1] as const;
// FOLD_EASE as a function (the page-change glide is driven frame by frame): cubic-bezier(0, 0, 0.2, 1), x solved by bisection
function foldCurve(p: number) {
  const x = (t: number) => 3 * (1 - t) * t * t * FOLD_EASE[2] + t * t * t;
  const y = (t: number) => 3 * (1 - t) * t * t * FOLD_EASE[3] + t * t * t;
  let lo = 0, hi = 1;
  for (let i = 0; i < 24; i++) { const m = (lo + hi) / 2; if (x(m) < p) lo = m; else hi = m; }
  return y((lo + hi) / 2);
}
// a page-change glide counts at most this much time per frame (a late frame slows it instead of jumping)
const GLIDE_STEP_MS = 34;

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
  // primed once the first measured height is set (at once): until then a change has nothing to glide from. Motion
  // animating `height` from an unset value made the first switch after a page opened snap (owner 2026-11-17), so the
  // glide is a CSS height transition on an explicit pixel height: FOLD_S (250 ms), the same ease-out.
  const [primed, setPrimed] = useState(false);
  useEffect(() => {
    if (h === null || primed) return;
    const id = requestAnimationFrame(() => setPrimed(true));
    return () => cancelAnimationFrame(id);
  }, [h, primed]);
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
  const own = (e: React.TransitionEvent) => e.target === e.currentTarget && e.propertyName === "height";
  return (
    // Calm: no set height at all, so a new size shows in the same frame as the new content
    <div className={className} data-auto-height data-moving={moving ? "1" : "0"}
      onTransitionRun={(e) => { if (own(e)) setMoving(true); }}
      onTransitionEnd={(e) => { if (own(e)) setMoving(false); }} onTransitionCancel={(e) => { if (own(e)) setMoving(false); }}
      style={{ overflow: moving && animated ? "hidden" : "visible", height: animated && h !== null ? h : "auto",
        transition: animated && primed ? `height ${FOLD_S}s cubic-bezier(${FOLD_EASE.join(", ")})` : "none" }}>
      <div ref={inner} className="flow-root">{children}</div>
    </div>
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
