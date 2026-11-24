// P&L calendar (SPEC §9b item 6; layout of docs/design/pnl_calendar_reference.png, its layout only), from the step-14a
// mock, on the server's month grid (src/nqbot/performance/pnl_calendar.py; the ledger guard applied there). Styling and
// motion from 21st.dev Activity Heatmap Calendar (36571), hover card (222) and sheet (35214) patterns. Colour is never
// alone: every amount carries its sign and arrow. A month whose backtest days are held back says why.
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";
import { arrow, money, pnlClass, rr } from "@/lib/format";
import { getJson } from "@/lib/api";
import { load, peek, prime } from "@/lib/prefetch";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/shell/page";
import { MonthPicker } from "./MonthPicker";
import type { CalCell, CalMonth, DayTrade, Unit } from "./types";

const MONTH_NAME = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
const SHORT = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const LONG = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const d12 = (iso: string) => new Date(iso + "T12:00:00Z");
/** a separator dot between the parts of one value (spaced by its flex row's gap, never by typed spaces) */
const Dot = () => <span className="text-text-3/70" aria-hidden="true">·</span>;
/** a signed amount: its arrow centred on the figures' height (not on the text baseline, where it sat low), then the
 * amount; colour is never alone (§9b): the sign and the arrow say it too */
function Signed({ v, text, className }: { v: number; text: string; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap", pnlClass(v), className)}>
      {arrow(v) && <span className="inline-block translate-y-[-0.04em] text-[0.58em] leading-none" aria-hidden="true">{arrow(v)}</span>}
      <span>{text}</span>
    </span>
  );
}
const ymAdd = (ym: string, n: number) => { const [y, m] = ym.split("-").map(Number); const d = new Date(Date.UTC(y, m - 1 + n, 1)); return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`; };

/** The calendar's data address; `month` null = the selection's latest month (the page loads it before an account switch
 * shows, owner 2026-11-16). */
export function calendarUrl({ account, unit, phase, month, plan }: { account: string; unit: Unit; phase: string; month: string | null; plan: string | null }) {
  return `/api/performance/calendar?${new URLSearchParams({ account, unit, phase, ...(month ? { month } : {}), ...(plan ? { plan } : {}) })}`;
}

/** Load a calendar month and keep the latest-month answer under its own month's address too. */
export function loadCalendar(args: Parameters<typeof calendarUrl>[0], signal?: AbortSignal) {
  return load<CalMonth>(calendarUrl(args), signal).then((d) => { if (!args.month) prime(calendarUrl({ ...args, month: d.month }), d); return d; });
}

// the month shown is the page's (`month`, `onMonth`), so an account switch keeps it and the page can load it ahead
export function PnlCalendar({ account, phase = "all", plan = null, unit, animated, label, month, onMonth }: {
  account: string; phase?: string; plan?: string | null; unit: Unit; animated: boolean; label: string; month: string | null; onMonth: (ym: string) => void;
}) {
  const setYm = onMonth;
  const [dir, setDir] = useState(1);
  const [got, setGot] = useState<CalMonth | null>(null);
  const [open, setOpen] = useState<{ date: string; trades: DayTrade[] | null } | null>(null);
  const [hover, setHover] = useState<{ cell: CalCell; x: number; y: number } | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    loadCalendar({ account, unit, phase, month, plan }, ctl.signal).then((d) => { setGot(d); if (!month) setYm(d.month); }).catch(() => {});
    return () => ctl.abort();
  }, [account, unit, month, plan, phase]); // eslint-disable-line react-hooks/exhaustive-deps
  // an answer already loaded shows in the same render (no placeholder); otherwise the old month stays until it arrives
  const data = peek<CalMonth>(calendarUrl({ account, unit, phase, month, plan })) ?? got;
  const ym = month ?? data?.month ?? null;
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
  // the month's stats (owner 2026-11-24, round 2): the main figure large, its parts smaller and muted beside it, on
  // one line that never truncates; five in a row with rules between them where the card is wide enough (a container
  // query, so it follows the card, not the window), else three and two (or two a row) without rules
  const stat = (lab: string, main: React.ReactNode, rest?: React.ReactNode) => (
    <div className="min-w-0 border-line-strong/50 @[1000px]/stats:px-5 @[1000px]/stats:first:pl-0 @[1000px]/stats:[&:not(:first-child)]:border-l" data-stat>
      <div className="eyebrow">{lab}</div>
      <div className="mt-1.5 flex h-6 items-center gap-2 whitespace-nowrap">
        <span className="font-num text-[17px] leading-6 text-text-1">{main}</span>
        {rest && <span className="flex items-center gap-1.5 text-[13px] text-text-2">{rest}</span>}
      </div>
    </div>
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
      <div className="@container/stats mb-5">
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 @[640px]/stats:grid-cols-3 @[1000px]/stats:grid-cols-5 @[1000px]/stats:gap-x-0" data-cal-stats>
          {stat("Monthly P&L", h.value === null ? "—" : <Signed v={h.value} text={fmt(h.value)} />)}
          {stat("Trading days", `${h.trading_days} days`, <><span className="text-pnl-up">{h.green_days} green</span><Dot /><span className="text-pnl-down">{h.red_days} red</span></>)}
          {stat("Trades", h.trades, <><span className="text-pnl-up">{h.wins} W</span><Dot /><span className="text-pnl-down">{h.losses} L</span></>)}
          {stat("Best day", h.best ? <Signed v={h.best.value} text={fmt(h.best.value)} /> : "—", h.best ? SHORT.format(d12(h.best.date)) : undefined)}
          {stat("Worst day", h.worst ? <Signed v={h.worst.value} text={fmt(h.worst.value)} /> : "—", h.worst ? SHORT.format(d12(h.worst.date)) : undefined)}
        </div>
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
                          "flex h-[96px] min-w-0 flex-col items-center justify-center gap-1 rounded-md border px-1.5 text-center transition-[border-color,transform] duration-150",
                          !has && "border-line-strong/40 bg-transparent",
                          has && c.sign === 1 && "border-pnl-up/25 bg-pnl-up/[.09] hover:border-pnl-up/60",
                          has && c.sign === -1 && "border-pnl-down/25 bg-pnl-down/[.09] hover:border-pnl-down/60",
                          has && c.sign === 0 && "border-line-strong/60 bg-bg-2 hover:border-text-3",
                          has && animated && "hover:-translate-y-0.5",
                        )}>
                        <span className="text-[12.5px] leading-4 text-text-2">{Number(c.date.slice(8))}</span>
                        {has && <>
                          <Signed v={v} text={fmt(v)} className="font-num text-[15px] leading-5" />
                          <span className="flex items-center gap-1.5 text-[11.5px] leading-4"><span className="text-text-3">{c.trades} trade{c.trades > 1 ? "s" : ""}</span><Dot /><span className="text-pnl-up">{c.wins} W</span><span className="text-pnl-down">{c.losses} L</span></span>
                        </>}
                      </button>
                    );
                  })}
                  <div className="flex h-[96px] min-w-0 flex-col items-center justify-center gap-1 rounded-md border border-line-strong/60 bg-bg-1 px-1.5 text-center" data-week>
                    <span className="text-[12px] leading-4 text-text-2">{SHORT.format(d12(inMonth[0].date))} – {SHORT.format(d12(inMonth[inMonth.length - 1].date)).split(" ")[1]}</span>
                    {w.week.trades ? <>
                      <Signed v={w.week.value ?? 0} text={fmt(w.week.value ?? 0)} className="font-num text-[15px] leading-5" />
                      <span className="flex items-center gap-1.5 text-[11.5px] leading-4 text-text-3"><span>{w.week.days_traded} days</span><Dot /><span>{w.week.trades} trades</span></span>
                      <span className="flex items-center gap-1.5 text-[11.5px] leading-4"><span className="text-pnl-up">{w.week.wins} W</span><span className="text-pnl-down">{w.week.losses} L</span><Dot /><span className="text-text-1">{w.week.win_rate === null ? "—" : `${Math.round(w.week.win_rate * 100)}%`}</span></span>
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
              // owner 2026-11-24 (round 2): each trade on two lines, its time, direction and style over its account, size
              // and prices, the net and R on the right; nothing shares or overflows a column at any width
              <>
                <div className="mt-4 flex items-baseline justify-between border-b border-line-strong/40 pb-2 text-[12px] text-text-3">
                  <span>{open.trades.length} trade{open.trades.length === 1 ? "" : "s"}</span><span>net · R</span>
                </div>
                <ul className="flex flex-col" data-day-trades>{open.trades.map((t) => (
                  <li key={t.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 border-b border-line-strong/40 py-2.5" data-day-trade>
                    <div className="flex min-w-0 items-center gap-2 text-[13px]">
                      <span className="font-num text-text-1">{t.time.slice(0, 5)}</span>
                      <span className={t.direction === "long" ? "text-pnl-up" : "text-pnl-down"}>{t.direction}</span>
                      <span className="text-text-2">({t.style})</span>
                    </div>
                    <span className={cn("text-right font-num text-[13px]", pnlClass(t.net_usd))}>{money(t.net_usd)}</span>
                    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-[12px] text-text-2">
                      <span>{t.account}</span><Dot /><span>{t.size}</span><Dot />
                      <span className="whitespace-nowrap font-num">{t.entry.toFixed(2)} → {t.exit.toFixed(2)}</span>
                    </div>
                    <span className={cn("text-right font-num text-[12px]", pnlClass(t.r))}>{rr(t.r)}</span>
                  </li>
                ))}</ul>
              </>
            )}
          </motion.aside>
        )}
      </AnimatePresence>
    </div>
  );
}
