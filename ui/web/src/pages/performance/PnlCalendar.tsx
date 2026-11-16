// P&L calendar (SPEC §9b item 6; layout of docs/design/pnl_calendar_reference.png, its layout only), from the step-14a
// mock, on the server's month grid (src/nqbot/performance/pnl_calendar.py; the ledger guard applied there). Styling and
// motion from 21st.dev Activity Heatmap Calendar (36571), hover card (222) and sheet (35214) patterns. Colour is never
// alone: every amount carries its sign and arrow. A month whose backtest days are held back says why.
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";
import { arrow, money, pnlClass, rr } from "@/lib/format";
import { getJson } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/shell/page";
import { MonthPicker } from "./MonthPicker";
import type { CalCell, CalMonth, DayTrade, Unit } from "./types";

const MONTH_NAME = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
const SHORT = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const LONG = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const d12 = (iso: string) => new Date(iso + "T12:00:00Z");
const ymAdd = (ym: string, n: number) => { const [y, m] = ym.split("-").map(Number); const d = new Date(Date.UTC(y, m - 1 + n, 1)); return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`; };

export function PnlCalendar({ account, phase = "all", plan = null, unit, animated, label }: { account: string; phase?: string; plan?: string | null; unit: Unit; animated: boolean; label: string }) {
  const [ym, setYm] = useState<string | null>(null);
  const [dir, setDir] = useState(1);
  const [data, setData] = useState<CalMonth | null>(null);
  const [open, setOpen] = useState<{ date: string; trades: DayTrade[] | null } | null>(null);
  const [hover, setHover] = useState<{ cell: CalCell; x: number; y: number } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    const q = new URLSearchParams({ account, unit, phase, ...(ym ? { month: ym } : {}), ...(plan ? { plan } : {}) });
    getJson<CalMonth>(`/api/performance/calendar?${q}`, ctl.signal).then((d) => { setData(d); if (!ym) setYm(d.month); }).catch(() => {});
    return () => ctl.abort();
  }, [account, unit, ym, plan, phase]);
  // a new phase opens on its own latest month
  useEffect(() => { setYm(null); }, [phase]);
  useEffect(() => { setOpen(null); }, [account, ym]);
  const openDay = (date: string) => {
    setOpen({ date, trades: null });
    getJson<{ trades: DayTrade[] }>(`/api/performance/day?${new URLSearchParams({ account, date, phase, ...(plan ? { plan } : {}) })}`).then((d) => setOpen({ date, trades: d.trades })).catch(() => {});
  };
  if (!data || !ym) return <Skeleton className="h-[560px]" />;
  const fmt = (v: number) => (unit === "usd" ? money(v) : rr(v));
  const months = data.months;
  const go = (step: number) => { setDir(step); setYm(ymAdd(ym, step)); };
  const h = data.header;
  const stat = (lab: string, body: React.ReactNode) => (
    <div className="min-w-0 border-line-strong/50 px-4 first:pl-0 [&:not(:first-child)]:border-l"><div className="eyebrow">{lab}</div><div className="mt-1 truncate font-mono text-[17px]">{body}</div></div>
  );
  return (
    <div className="relative" data-cal>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h3 className="font-display text-[26px] leading-none text-text-1">P&amp;L calendar <span className="italic text-cream">{MONTH_NAME.format(d12(ym + "-15"))}</span></h3>
        <span className="rounded-full border border-line-strong px-2 py-0.5 font-mono text-[10px] tracking-[0.08em] text-text-2">{label}</span>
        <div className="ml-auto">
          <MonthPicker value={ym} months={months} animated={animated} onStep={go} onChange={(m) => { setDir(m > ym ? 1 : -1); setYm(m); }} />
        </div>
      </div>
      {data.notice && (
        <p className="mb-3 rounded-sm border border-info/40 bg-info/10 px-3 py-2 text-[13px] text-text-1" role="note" data-cal-notice>{data.notice.reason}</p>
      )}
      <div className="mb-4 grid grid-cols-2 gap-y-3 min-[1100px]:grid-cols-5">
        {stat("Monthly P&L", h.value === null ? "—" : <span className={pnlClass(h.value)}><span className="mr-1 text-[.7em]">{arrow(h.value)}</span>{fmt(h.value)}</span>)}
        {stat("Trading days", <>{h.trading_days}d · <span className="text-pnl-up">{h.green_days} green</span> · <span className="text-pnl-down">{h.red_days} red</span></>)}
        {stat("Trades", <>{h.trades} · <span className="text-pnl-up">{h.wins}W</span> · <span className="text-pnl-down">{h.losses}L</span></>)}
        {stat("Best day", h.best ? <span className={pnlClass(h.best.value)}>{arrow(h.best.value)} {fmt(h.best.value)} · {SHORT.format(d12(h.best.date))}</span> : "—")}
        {stat("Worst day", h.worst ? <span className={pnlClass(h.worst.value)}>{arrow(h.worst.value)} {fmt(h.worst.value)} · {SHORT.format(d12(h.worst.date))}</span> : "—")}
      </div>
      <div className="grid grid-cols-6 gap-2 pb-1.5 text-center text-[12px] text-text-2">{["Mon", "Tue", "Wed", "Thu", "Fri", "Week"].map((d) => <div key={d}>{d}</div>)}</div>
      <div className="relative overflow-hidden">
        <AnimatePresence mode="popLayout" initial={false} custom={dir}>
          <motion.div key={ym + unit + account} custom={dir}
            initial={animated ? { opacity: 0, x: dir * 12 } : false} animate={{ opacity: 1, x: 0 }} exit={animated ? { opacity: 0, x: dir * -12 } : { opacity: 0, transition: { duration: 0 } }}
            transition={{ duration: animated ? 0.24 : 0, ease: [0.2, 0, 0, 1] }} className="grid gap-2">
            {data.weeks.map((w, wi) => {
              const inMonth = w.days.filter((c) => c.in_month);
              if (!inMonth.length) return null;
              return (
                <div key={wi} className="grid grid-cols-6 gap-2">
                  {w.days.map((c) => {
                    if (!c.in_month) return <div key={c.date} />;
                    const has = c.value !== null, v = c.value ?? 0;
                    return (
                      <button key={c.date} type="button" disabled={!has} data-day={c.date} data-sign={c.sign ?? "none"}
                        onClick={() => has && openDay(c.date)}
                        onMouseEnter={(e) => { if (!has) return; const r = e.currentTarget.getBoundingClientRect(), p = (e.currentTarget.closest("[data-cal]") as HTMLElement).getBoundingClientRect(); setHover({ cell: c, x: r.left - p.left + r.width / 2, y: r.top - p.top }); }}
                        onMouseLeave={() => setHover(null)}
                        className={cn(
                          "flex h-[92px] flex-col items-center justify-center gap-0.5 rounded-md border text-center transition-[border-color,transform] duration-150",
                          !has && "border-line-strong/40 bg-transparent",
                          has && c.sign === 1 && "border-pnl-up/25 bg-pnl-up/[.09] hover:border-pnl-up/60",
                          has && c.sign === -1 && "border-pnl-down/25 bg-pnl-down/[.09] hover:border-pnl-down/60",
                          has && c.sign === 0 && "border-line-strong/60 bg-bg-2 hover:border-text-3",
                          has && animated && "hover:-translate-y-0.5",
                        )}>
                        <span className="text-[13px] text-text-2">{Number(c.date.slice(8))}</span>
                        {has && <>
                          <span className={cn("font-mono text-[15px]", pnlClass(v))}><span className="mr-0.5 text-[.7em]">{arrow(v)}</span>{fmt(v)}</span>
                          <span className="text-[11.5px] text-text-3">{c.trades} trade{c.trades > 1 ? "s" : ""}</span>
                          <span className="text-[11.5px]"><span className="text-pnl-up">{c.wins} W</span><span className="text-text-3"> · </span><span className="text-pnl-down">{c.losses} L</span></span>
                        </>}
                      </button>
                    );
                  })}
                  <div className="flex h-[92px] flex-col items-center justify-center gap-0.5 rounded-md border border-line-strong/60 bg-bg-1 text-center" data-week>
                    <span className="text-[12px] text-text-2">{SHORT.format(d12(inMonth[0].date))} – {SHORT.format(d12(inMonth[inMonth.length - 1].date)).split(" ")[1]}</span>
                    {w.week.trades ? <>
                      <span className={cn("font-mono text-[15px]", pnlClass(w.week.value ?? 0))}><span className="mr-0.5 text-[.7em]">{arrow(w.week.value ?? 0)}</span>{fmt(w.week.value ?? 0)}</span>
                      <span className="text-[11.5px] text-text-3">{w.week.days_traded} days · {w.week.trades} trades</span>
                      <span className="text-[11.5px]"><span className="text-pnl-up">{w.week.wins} W</span> · <span className="text-pnl-down">{w.week.losses} L</span> · <span className="text-text-1">{w.week.win_rate === null ? "—" : `${Math.round(w.week.win_rate * 100)}%`}</span></span>
                    </> : <span className="text-[12px] text-text-3">No trades</span>}
                  </div>
                </div>
              );
            })}
          </motion.div>
        </AnimatePresence>
      </div>
      {hover && !open && (
        <div className="pointer-events-none absolute z-30 w-[220px] -translate-x-1/2 -translate-y-full rounded-sm border border-line-strong bg-bg-3 p-3 shadow-[0_12px_32px_rgba(0,0,0,.55)]" style={{ left: hover.x, top: hover.y - 6 }}>
          <div className="eyebrow">{LONG.format(d12(hover.cell.date))}</div>
          <div className="mt-1.5 flex justify-between font-mono text-[12px]"><span className="text-text-2">{hover.cell.trades} trades · {hover.cell.wins} W · {hover.cell.losses} L</span><span className={pnlClass(hover.cell.value ?? 0)}>{fmt(hover.cell.value ?? 0)}</span></div>
          <div className="mt-2 text-[11px] text-text-3">Click for the day&apos;s trades</div>
        </div>
      )}
      <AnimatePresence>
        {open && (
          <motion.aside initial={animated ? { x: 40, opacity: 0 } : false} animate={{ x: 0, opacity: 1 }} exit={animated ? { x: 40, opacity: 0 } : { opacity: 0, transition: { duration: 0 } }} transition={{ duration: animated ? 0.25 : 0 }}
            className="absolute right-0 top-0 z-40 h-full w-[min(460px,100%)] overflow-y-auto rounded-md border border-line-strong bg-bg-3 p-4 shadow-[0_24px_64px_rgba(0,0,0,.7)]" role="dialog" aria-label="Day trades" data-day-panel>
            <div className="flex items-center justify-between">
              <div><div className="eyebrow">Day</div><div className="font-display text-[24px] leading-none">{LONG.format(d12(open.date))}</div></div>
              <button type="button" aria-label="Close" onClick={() => setOpen(null)} className="grid size-8 place-items-center rounded-sm border border-line-strong text-text-2 hover:text-text-1"><X className="size-4" /></button>
            </div>
            {!open.trades ? <Skeleton className="mt-4 h-32" /> : (
              <table className="mt-4 w-full text-[12.5px]">
                <thead className="text-left text-text-3"><tr><th className="py-1 font-normal">Time</th><th className="font-normal">Account</th><th className="font-normal">Dir</th><th className="font-normal">Style</th><th className="font-normal">Size</th><th className="text-right font-normal">Entry → exit</th><th className="text-right font-normal">Net</th><th className="text-right font-normal">R</th></tr></thead>
                <tbody className="font-mono">{open.trades.map((t) => (
                  <tr key={t.id} className="border-t border-line-strong/40">
                    <td className="py-1.5">{t.time.slice(0, 5)}</td><td>{t.account}</td><td>{t.direction}</td><td>({t.style})</td><td className="font-sans text-text-2">{t.size}</td>
                    <td className="text-right text-text-2">{t.entry.toFixed(2)} → {t.exit.toFixed(2)}</td>
                    <td className={cn("text-right", pnlClass(t.net_usd))}>{money(t.net_usd)}</td><td className={cn("text-right", pnlClass(t.r))}>{rr(t.r)}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </motion.aside>
        )}
      </AnimatePresence>
    </div>
  );
}
