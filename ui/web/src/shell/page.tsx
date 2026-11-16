// Shared page parts (pages/shell.md): the page header (eyebrow, two-line title with the second line in cream italic,
// subtitle), the empty state (Dot Matrix Text, a one-line message, the next action; never urgent information) and
// skeletons shaped like the content.
import type { ReactNode } from "react";
import { motion } from "motion/react";
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
