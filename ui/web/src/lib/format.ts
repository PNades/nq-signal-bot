// Number formatting (design-system/MASTER.md §12): true minus, sign always on deltas, prices to the tick, ET times.
// Tabular figures come from the mono font and `font-variant-numeric: tabular-nums` on body.
const usd2 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const usd0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
export const MINUS = "−";
export const money = (v: number, signed = true) => `${v < 0 ? MINUS : signed && v > 0 ? "+" : ""}$${usd2.format(Math.abs(v))}`;
export const money0 = (v: number) => `${v < 0 ? MINUS : ""}$${usd0.format(Math.abs(v))}`;
export const rr = (v: number) => `${v < 0 ? MINUS : v > 0 ? "+" : "±"}${Math.abs(v).toFixed(2)}R`;
export const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
export const price = (v: number) => usd2.format(v);
export const arrow = (v: number) => (v > 0 ? "▲" : v < 0 ? "▼" : "");
export const pnlClass = (v: number) => (v > 0 ? "text-pnl-up" : v < 0 ? "text-pnl-down" : "text-text-2");
const etTime = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const etDate = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", weekday: "short", day: "numeric", month: "short" });
export const etClock = (d: Date) => etTime.format(d);
export const etDay = (d: Date) => etDate.format(d).replace(",", "");
