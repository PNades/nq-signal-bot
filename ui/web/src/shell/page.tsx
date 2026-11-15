// Shared page parts (pages/shell.md): the page header (eyebrow, two-line title with the second line in cream italic,
// subtitle), the empty state (Dot Matrix Text, a one-line message, the next action; never urgent information) and
// skeletons shaped like the content.
import type { ReactNode } from "react";
import { DotMatrixText } from "@/components/ui/dot-text";
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
