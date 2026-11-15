// The Effects setting (MASTER.md §8): Full or Calm. Calm when chosen, or forced by prefers-reduced-motion.
import { createContext, useContext } from "react";

export type Effects = "full" | "calm";
export const EffectsContext = createContext<Effects>("full");
export const useEffectsMode = () => useContext(EffectsContext);
export const useAnimated = () => useContext(EffectsContext) === "full";

// Number formatting (MASTER.md §12): true minus, sign always on deltas, tabular figures come from the font.
const usd0 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const money = (v: number, signed = true) => `${v < 0 ? "−" : signed && v > 0 ? "+" : ""}$${usd0.format(Math.abs(v))}`;
export const rr = (v: number) => `${v < 0 ? "−" : v > 0 ? "+" : "±"}${Math.abs(v).toFixed(2)}R`;
export const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
export const arrow = (v: number) => (v > 0 ? "▲" : v < 0 ? "▼" : "");
export const pnlClass = (v: number) => (v > 0 ? "text-pnl-up" : v < 0 ? "text-pnl-down" : "text-text-2");
