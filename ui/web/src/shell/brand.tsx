// CreamFlow brand (design-system/MASTER.md §2, design-system/brand/): the logo mark (a cream "C" open to the right,
// a gold flow line leaving it, an ember dot) and the wordmark ("Cream" upright, "Flow" italic).
import { useId } from "react";
import { cn } from "@/lib/utils";

export function LogoMark({ size = 28, className }: { size?: number; className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true" className={className}>
      <defs>
        <linearGradient id={`${id}-g`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#FBF1DC" />
          <stop offset="1" stopColor="#E9D3A6" />
        </linearGradient>
      </defs>
      <path d="M46.6 17.4 A21 21 0 1 0 46.6 46.6" fill="none" stroke={`url(#${id}-g)`} strokeWidth="9" strokeLinecap="round" />
      <path d="M24 37 C30 37 31 27 37 27 S44 35 50 31 L56 26" fill="none" stroke="#E7BF72" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="56" cy="26" r="3.2" fill="#FF9A3C" />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("font-display leading-none tracking-[-0.01em] text-cream", className)}>
      Cream<span className="italic text-gold">Flow</span>
    </span>
  );
}
