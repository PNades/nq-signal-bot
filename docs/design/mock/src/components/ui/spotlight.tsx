"use client";
// Spotlight: 21st.dev id 35180 (kevingirelli), code read 2026-11-15: no network calls. CreamFlow changes: no Radix Slot
// (the optional asChild mode is dropped), the light in cream, off in Calm (`enabled`).
import { useEffect, useRef, type ComponentProps, type CSSProperties } from "react";
import { cn } from "@/lib/utils";

const RING_MASK = "linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0)";
const lightClassName = cn(
  "pointer-events-none absolute inset-0 -z-10 rounded-[inherit] opacity-0",
  "transition-opacity duration-300 motion-reduce:transition-none",
  "group-focus-within/spotlight:opacity-100 group-hover/spotlight:opacity-100",
);

interface SpotlightProps extends ComponentProps<"div"> { size?: number; color?: string; ring?: boolean; enabled?: boolean }

export function Spotlight({ size = 320, color = "var(--color-cream)", ring = true, enabled = true, className, children, onPointerMove, ...props }: SpotlightProps) {
  const frame = useRef(0);
  useEffect(() => () => cancelAnimationFrame(frame.current), []);
  const circle = `${size}px circle at var(--spotlight-x, 50%) var(--spotlight-y, 50%)`;
  const ringStyle: CSSProperties = {
    background: `radial-gradient(${circle}, color-mix(in oklab, ${color} 45%, transparent), transparent 60%)`,
    mask: RING_MASK, maskComposite: "exclude", WebkitMask: RING_MASK, WebkitMaskComposite: "xor",
  };
  return (
    <div
      data-slot="spotlight"
      className={cn("group/spotlight relative isolate", className)}
      onPointerMove={(event) => {
        onPointerMove?.(event);
        if (!enabled || event.pointerType === "touch") return;
        const target = event.currentTarget, { clientX, clientY } = event;
        cancelAnimationFrame(frame.current);
        frame.current = requestAnimationFrame(() => {
          const b = target.getBoundingClientRect();
          target.style.setProperty("--spotlight-x", `${clientX - b.left}px`);
          target.style.setProperty("--spotlight-y", `${clientY - b.top}px`);
        });
      }}
      {...props}
    >
      {enabled && <span aria-hidden="true" className={lightClassName} style={{ background: `radial-gradient(${circle}, color-mix(in oklab, ${color} 9%, transparent), transparent 70%)` }} />}
      {enabled && ring ? <span aria-hidden="true" className={cn(lightClassName, "-inset-px p-px")} style={ringStyle} /> : null}
      {children}
    </div>
  );
}

export default Spotlight;
