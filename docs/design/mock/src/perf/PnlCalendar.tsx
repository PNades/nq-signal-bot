// P&L calendar (SPEC §9b item 6; layout of docs/design/pnl_calendar_reference.png, its layout only). Styling and
// motion drawn from 21st.dev Activity Heatmap Calendar (36571: tinted day levels, hover tooltips, staggered moves),
// hover card (222) and sheet (35214) patterns. Colour is never alone: every amount carries its sign and arrow.
import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { type Day } from "./data";
import { arrow, money, pnlClass, rr } from "@/effects";
import { cn } from "@/lib/utils";

const MONTH_NAME = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
const SHORT = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

function monthGrid(ym: string, DAYS: Day[]) {
  const [y, m] = ym.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1)), last = new Date(Date.UTC(y, m, 0));
  const byDate = new Map(DAYS.map((d) => [d.date, d]));
  const weeks: { days: ({ date: string; day?: Day } | null)[] }[] = [];
  let week: ({ date: string; day?: Day } | null)[] = [null, null, null, null, null];
  for (let d = new Date(first); d <= last; d.setUTCDate(d.getUTCDate() + 1)) {
    const wd = d.getUTCDay();
    if (wd === 0 || wd === 6) continue;
    const key = d.toISOString().slice(0, 10);
    week[wd - 1] = { date: key, day: byDate.get(key) };
    if (wd === 5) { weeks.push({ days: week }); week = [null, null, null, null, null]; }
  }
  if (week.some(Boolean)) weeks.push({ days: week });
  return { label: MONTH_NAME.format(first), weeks };
}

export function PnlCalendar({ unit, animated, days: DAYS }: { unit: "usd" | "r"; animated: boolean; days: Day[] }) {
  const months = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"];
  const [mi, setMi] = useState(5);
  const [dir, setDir] = useState(1);
  const [open, setOpen] = useState<Day | null>(null);
  const [hover, setHover] = useState<{ day: Day; x: number; y: number } | null>(null);
  const ym = months[mi];
  const grid = useMemo(() => monthGrid(ym, DAYS), [ym, DAYS]);
  const val = (d: { usd: number; r: number }) => (unit === "usd" ? d.usd : d.r);
  const fmt = (v: number) => (unit === "usd" ? money(v) : rr(v));
  const mDays = DAYS.filter((d) => d.date.startsWith(ym) && d.trades.length);
  const mTrades = mDays.flatMap((d) => d.trades);
  const best = mDays.reduce<Day | null>((a, d) => (!a || val(d) > val(a) ? d : a), null);
  const worst = mDays.reduce<Day | null>((a, d) => (!a || val(d) < val(a) ? d : a), null);
  const total = mDays.reduce((a, d) => a + val(d), 0);
  const go = (step: number) => { setDir(step); setMi((i) => Math.min(months.length - 1, Math.max(0, i + step))); setOpen(null); };

  const stat = (label: string, body: React.ReactNode) => (
    <div className="min-w-0 border-line-strong/50 px-4 first:pl-0 [&:not(:first-child)]:border-l"><div className="eyebrow">{label}</div><div className="mt-1 truncate font-mono text-[17px]">{body}</div></div>
  );

  return (
    <div className="relative" data-cal>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h3 className="font-display text-[26px] leading-none text-text-1">P&amp;L calendar <span className="italic text-cream">{grid.label}</span></h3>
        <span className="rounded-full border border-line-strong px-2 py-0.5 font-mono text-[10px] tracking-[0.08em] text-text-2">BACKTEST · MOCK DATA</span>
        <div className="ml-auto flex items-center gap-1">
          <button aria-label="Previous month" disabled={mi === 0} onClick={() => go(-1)} className="grid size-8 place-items-center rounded-sm border border-line-strong text-text-2 hover:text-text-1 disabled:opacity-30"><ChevronLeft className="size-4" /></button>
          <button aria-label="Next month" disabled={mi === months.length - 1} onClick={() => go(1)} className="grid size-8 place-items-center rounded-sm border border-line-strong text-text-2 hover:text-text-1 disabled:opacity-30"><ChevronRight className="size-4" /></button>
        </div>
      </div>
      <div className="mb-4 grid grid-cols-2 gap-y-3 min-[1100px]:grid-cols-5">
        {stat("Monthly P&L", <span className={pnlClass(total)}><span className="mr-1 text-[.7em]">{arrow(total)}</span>{fmt(total)}</span>)}
        {stat("Trading days", <>{mDays.length}d · <span className="text-pnl-up">{mDays.filter((d) => val(d) > 0).length} green</span> · <span className="text-pnl-down">{mDays.filter((d) => val(d) < 0).length} red</span></>)}
        {stat("Trades", <>{mTrades.length} · <span className="text-pnl-up">{mTrades.filter((t) => t.usd > 0).length}W</span> · <span className="text-pnl-down">{mTrades.filter((t) => t.usd < 0).length}L</span></>)}
        {stat("Best day", best ? <span className="text-pnl-up">▲ {fmt(val(best))} · {SHORT.format(new Date(best.date))}</span> : "—")}
        {stat("Worst day", worst ? <span className={pnlClass(val(worst))}>{arrow(val(worst))} {fmt(val(worst))} · {SHORT.format(new Date(worst.date))}</span> : "—")}
      </div>
      <div className="grid grid-cols-6 gap-2 pb-1.5 text-center text-[12px] text-text-2">{["Mon", "Tue", "Wed", "Thu", "Fri", "Week"].map((d) => <div key={d}>{d}</div>)}</div>
      <div className="relative overflow-hidden">
        <AnimatePresence mode="popLayout" initial={false} custom={dir}>
          <motion.div key={ym} custom={dir}
            initial={animated ? { opacity: 0, x: dir * 24 } : false} animate={{ opacity: 1, x: 0 }} exit={animated ? { opacity: 0, x: dir * -24 } : { opacity: 0 }}
            transition={{ duration: animated ? 0.28 : 0, ease: [0.2, 0, 0, 1] }} className="grid gap-2">
            {grid.weeks.map((w, wi) => {
              const wd = w.days.filter((c): c is { date: string; day?: Day } => !!c).map((c) => c.day).filter((d): d is Day => !!d && d.trades.length > 0);
              const wsum = wd.reduce((a, d) => a + val(d), 0), wt = wd.flatMap((d) => d.trades);
              const ww = wt.filter((t) => t.usd > 0).length, wl = wt.filter((t) => t.usd < 0).length;
              return (
                <div key={wi} className="grid grid-cols-6 gap-2">
                  {w.days.map((c, ci) => {
                    if (!c) return <div key={ci} />;
                    const d = c.day, has = d && d.trades.length > 0, v = has ? val(d!) : 0;
                    return (
                      <button key={ci} type="button" disabled={!has}
                        onClick={() => has && setOpen(d!)}
                        onMouseEnter={(e) => { if (!has) return; const r = (e.currentTarget as HTMLElement).getBoundingClientRect(), p = (e.currentTarget.closest("[data-cal]") as HTMLElement).getBoundingClientRect(); setHover({ day: d!, x: r.left - p.left + r.width / 2, y: r.top - p.top }); }}
                        onMouseLeave={() => setHover(null)}
                        className={cn(
                          "flex h-[92px] flex-col items-center justify-center gap-0.5 rounded-md border text-center transition-[border-color,transform] duration-150",
                          !has && "border-line-strong/40 bg-transparent",
                          has && v > 0 && "border-pnl-up/25 bg-pnl-up/[.09] hover:border-pnl-up/60",
                          has && v < 0 && "border-pnl-down/25 bg-pnl-down/[.09] hover:border-pnl-down/60",
                          has && v === 0 && "border-line-strong/60 bg-bg-2 hover:border-text-3",
                          has && animated && "hover:-translate-y-0.5",
                        )}>
                        <span className="text-[13px] text-text-2">{Number(c.date.slice(8))}</span>
                        {has && <>
                          <span className={cn("font-mono text-[15px]", pnlClass(v))}><span className="mr-0.5 text-[.7em]">{arrow(v)}</span>{fmt(v)}</span>
                          <span className="text-[11.5px] text-text-3">{d!.trades.length} trade{d!.trades.length > 1 ? "s" : ""}</span>
                          <span className="text-[11.5px]"><span className="text-pnl-up">{d!.wins} W</span><span className="text-text-3"> · </span><span className="text-pnl-down">{d!.losses} L</span></span>
                        </>}
                      </button>
                    );
                  })}
                  <div className="flex h-[92px] flex-col items-center justify-center gap-0.5 rounded-md border border-line-strong/60 bg-bg-1 text-center">
                    <span className="text-[12px] text-text-2">{SHORT.format(new Date(w.days.find(Boolean)!.date))} – {SHORT.format(new Date([...w.days].reverse().find(Boolean)!.date)).split(" ")[1]}</span>
                    {wt.length ? <>
                      <span className={cn("font-mono text-[15px]", pnlClass(wsum))}><span className="mr-0.5 text-[.7em]">{arrow(wsum)}</span>{fmt(wsum)}</span>
                      <span className="text-[11.5px] text-text-3">{wd.length} days · {wt.length} trades</span>
                      <span className="text-[11.5px]"><span className="text-pnl-up">{ww} W</span> · <span className="text-pnl-down">{wl} L</span> · <span className="text-text-1">{ww + wl ? Math.round((ww / (ww + wl)) * 100) : 0}%</span></span>
                    </> : <span className="text-[12px] text-text-3">No trades</span>}
                  </div>
                </div>
              );
            })}
          </motion.div>
        </AnimatePresence>
      </div>
      {hover && !open && (
        <div className="pointer-events-none absolute z-30 w-[230px] -translate-x-1/2 -translate-y-full rounded-sm border border-line-strong bg-bg-3 p-3 shadow-[0_12px_32px_rgba(0,0,0,.55)]" style={{ left: hover.x, top: hover.y - 6 }}>
          <div className="eyebrow">{new Date(hover.day.date).toUTCString().slice(0, 16)}</div>
          {hover.day.trades.slice(0, 3).map((t) => (
            <div key={t.id} className="mt-1.5 flex justify-between font-mono text-[12px]"><span className="text-text-2">{t.time} {t.dir} {t.style}</span><span className={pnlClass(t.usd)}>{unit === "usd" ? money(t.usd) : rr(t.r)}</span></div>
          ))}
          <div className="mt-2 text-[11px] text-text-3">Click for the day's trades</div>
        </div>
      )}
      <AnimatePresence>
        {open && (
          <motion.aside initial={animated ? { x: 40, opacity: 0 } : false} animate={{ x: 0, opacity: 1 }} exit={{ x: 40, opacity: 0 }} transition={{ duration: animated ? 0.25 : 0 }}
            className="absolute right-0 top-0 z-40 h-full w-[min(420px,100%)] overflow-y-auto rounded-md border border-line-strong bg-bg-3 p-4 shadow-[0_24px_64px_rgba(0,0,0,.7)]" role="dialog" aria-label="Day trades">
            <div className="flex items-center justify-between">
              <div><div className="eyebrow">Day</div><div className="font-display text-[24px] leading-none">{new Date(open.date).toUTCString().slice(0, 16)}</div></div>
              <button aria-label="Close" onClick={() => setOpen(null)} className="grid size-8 place-items-center rounded-sm border border-line-strong text-text-2 hover:text-text-1"><X className="size-4" /></button>
            </div>
            <table className="mt-4 w-full text-[12.5px]">
              <thead className="text-left text-text-3"><tr><th className="py-1 font-normal">Time</th><th className="font-normal">Acct</th><th className="font-normal">Dir</th><th className="font-normal">Style</th><th className="text-right font-normal">Net</th><th className="text-right font-normal">R</th></tr></thead>
              <tbody className="font-mono">{open.trades.map((t) => (
                <tr key={t.id} className="border-t border-line-strong/40"><td className="py-1.5">{t.time}</td><td>{t.account}</td><td>{t.dir}</td><td>({t.style})</td><td className={cn("text-right", pnlClass(t.usd))}>{money(t.usd)}</td><td className={cn("text-right", pnlClass(t.r))}>{rr(t.r)}</td></tr>
              ))}</tbody>
            </table>
          </motion.aside>
        )}
      </AnimatePresence>
    </div>
  );
}
