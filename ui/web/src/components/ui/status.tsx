// Status: 21st.dev id 25395 (diceui), code read 2026-11-15: no network calls, no remote assets.
// CreamFlow changes: the variants are the MASTER §3.4 state tokens (ok / warn / crit / info / stale), never accent
// colours; the indicator's ping runs only for a healthy state in Full (MASTER §10: a bad state never blinks for
// attention more than once); radix-ui's Slot (asChild) dropped; mono uppercase label (the top-bar pill).
import type * as React from "react";
import { cn } from "@/lib/utils";

export type StatusVariant = "ok" | "warn" | "crit" | "info" | "stale";

const variants: Record<StatusVariant, string> = {
  ok: "border-ok/30 bg-ok/10 text-ok [&_[data-slot=status-indicator]]:bg-ok",
  warn: "border-warn/30 bg-warn/10 text-warn [&_[data-slot=status-indicator]]:bg-warn",
  crit: "border-crit/40 bg-crit/10 text-crit [&_[data-slot=status-indicator]]:bg-crit",
  info: "border-info/30 bg-info/10 text-info [&_[data-slot=status-indicator]]:bg-info",
  stale: "border-line-strong bg-transparent text-text-2 [&_[data-slot=status-indicator]]:bg-text-3",
};

export function Status({ variant = "stale", className, ...props }: React.ComponentProps<"div"> & { variant?: StatusVariant }) {
  return (
    <div data-slot="status" data-variant={variant} {...props}
      className={cn("inline-flex h-7 w-fit shrink-0 items-center gap-2 overflow-hidden whitespace-nowrap rounded-full border px-3 font-mono text-[11px] tracking-[0.08em]", variants[variant], className)} />
  );
}

export function StatusIndicator({ pulse = false, className, ...props }: React.ComponentProps<"span"> & { pulse?: boolean }) {
  return (
    <span data-slot="status-indicator" aria-hidden="true" {...props}
      className={cn("relative flex size-1.5 shrink-0 rounded-full",
        pulse && "before:absolute before:inset-0 before:animate-ping before:rounded-full before:bg-inherit before:opacity-60", className)} />
  );
}

export function StatusLabel({ className, ...props }: React.ComponentProps<"span">) {
  return <span data-slot="status-label" {...props} className={cn("leading-none", className)} />;
}
