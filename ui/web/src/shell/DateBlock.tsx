// The date block from the old sidebar's foot (owner 2026-11-15): the large serif day number with "NOV 2026" and
// "SAT · EASTERN" beside it, a small fixed element in the bottom-left corner of every page, Home included. New York's
// date (the trading date), recomputed from the clock every second, so it turns over at midnight ET by itself.
// It never covers content: every page keeps at least DATE_BLOCK_PAD of bottom room (AppFrame), and it hides below
// DATE_BLOCK_MIN_W px, where it would crowd the dock (the top bar's clock still shows the time).
import { useNow } from "@/lib/hooks";

/** bottom offset 16 + block 44 + air 16 */
export const DATE_BLOCK_PAD = 76;
/** below this window width the block hides (the open dock's left edge comes within ~80 px of it at 900 px) */
export const DATE_BLOCK_MIN_W = 900;

const part = (d: Date, o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", ...o }).format(d);

export function DateBlock() {
  const now = useNow();
  const day = part(now, { day: "numeric" });
  const month = part(now, { month: "short" }).toUpperCase();
  const year = part(now, { year: "numeric" });
  const weekday = part(now, { weekday: "short" }).toUpperCase();
  return (
    <div data-date-block aria-label={`${part(now, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}, Eastern time`}
      className="pointer-events-none fixed bottom-4 left-4 z-[20] hidden h-11 select-none items-center gap-2.5 rounded-md border border-white/[.06] bg-bg-0/85 px-3 backdrop-blur-md min-[900px]:flex">
      <span className="font-display text-[32px] leading-none text-text-1">{day}</span>
      <span className="eyebrow leading-[14px]">{month} {year}<br />{weekday} · EASTERN</span>
    </div>
  );
}
