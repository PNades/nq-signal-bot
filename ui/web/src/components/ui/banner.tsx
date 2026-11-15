// Alert banner: the layout of 21st.dev Alert id 34383 (appica-dev; code read 2026-11-15: no network calls; its
// alert-utils helper files weren't returned by get_component): icon, title, description and actions in one grid row.
// CreamFlow changes (MASTER §10, pages/shell.md): critical and warning banners sit under the ticker tape, stay until
// acknowledged, stack newest first, and appear at once: the component's blur/scale exit animation is dropped, and no
// glass. A crit banner carries the OctagonAlert icon, its words and a 2-px left rule; colour is never the only cue.
import type * as React from "react";
import { Info, OctagonAlert, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";

export type BannerKind = "crit" | "warn" | "info";
const ICON = { crit: OctagonAlert, warn: TriangleAlert, info: Info } as const;
const TONE: Record<BannerKind, string> = {
  crit: "border-l-crit bg-[color-mix(in_oklab,var(--color-crit)_10%,var(--color-bg-0))] [&_[data-slot=banner-icon]]:text-crit",
  warn: "border-l-warn bg-[color-mix(in_oklab,var(--color-warn)_8%,var(--color-bg-0))] [&_[data-slot=banner-icon]]:text-warn",
  info: "border-l-info bg-[color-mix(in_oklab,var(--color-info)_7%,var(--color-bg-0))] [&_[data-slot=banner-icon]]:text-info",
};

export function Banner({ kind, title, children, actions, className, ...props }: Omit<React.HTMLAttributes<HTMLDivElement>, "title"> & {
  kind: BannerKind; title: React.ReactNode; actions?: React.ReactNode;
}) {
  const Icon = ICON[kind];
  return (
    <div role={kind === "crit" ? "alert" : "status"} data-kind={kind} {...props}
      className={cn("grid grid-cols-[auto_1fr_auto] items-center gap-x-3 border-b border-l-2 border-b-line px-5 py-2.5", TONE[kind], className)}>
      <Icon data-slot="banner-icon" className="size-[18px] shrink-0" aria-hidden="true" />
      <div className="min-w-0 text-[13.5px] leading-5">
        <span className="font-semibold text-text-1">{title}</span>
        {children && <span className="ml-2 text-text-2">{children}</span>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
