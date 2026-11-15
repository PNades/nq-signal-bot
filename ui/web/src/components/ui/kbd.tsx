// Combo Kbd: 21st.dev id 28308 (ai2), code read 2026-11-15: static, no network calls. CreamFlow: only the ComboKbd
// variant, on the Cocoa surfaces; Windows key names (Ctrl, never ⌘; owner 2026-11-14).
import type * as React from "react";
import { cn } from "@/lib/utils";

export function Kbd({ keys, className, ...props }: React.ComponentProps<"kbd"> & { keys: string[] }) {
  return (
    <kbd data-slot="kbd" {...props} className={cn("inline-flex w-fit shrink-0 select-none items-center gap-1 whitespace-nowrap font-mono font-medium leading-none text-text-2", className)}>
      <span className="sr-only">{keys.join(" plus ")}</span>
      <span aria-hidden="true" className="inline-flex items-center gap-1">
        {keys.map((k, i) => (
          <kbd key={`${k}-${i}`} className="inline-flex h-5 min-w-5 items-center justify-center rounded-xs border border-line-strong bg-bg-2 px-1.5 text-[11px] leading-none text-text-2">{k}</kbd>
        ))}
      </span>
    </kbd>
  );
}
