// Performance (SPEC §9b item 6, design-system/pages/performance.md), from the step-14a mock, on the real API: the
// stored ledgers (none before step 15: "no data yet") or, under `nqbot ui --synthetic`, the synthetic ledger, always
// through the ledger guard (walk-forward and sealed months held back with the reason and the missing gate).
// Picks used: KPI Tile (40086), Animated Tabs (24930), Spotlight (35180), Border Beam Panel (23408), the Halftone
// Wordmark Footer (37628), the P&L calendar after the reference layout, Lightweight Charts for equity and drawdown.
// Backtest phases are about the strategy (owner 2026-11-15): the header's filter is a plan selector and every number
// (KPIs, stats, Equity, Drawdown, Monte Carlo, Calendar, Trades) follows the plan's dollars; R doesn't change by plan.
// Real-trades phases (demo, prop, live): the account filter and the top-bar account switcher are one selection, in
// sync both ways. All phases shows both (the plan prices the backtest part, the account filters the real trades).
import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, type Variants } from "motion/react";
import { CalendarRange, Download, Info, Layers, Route, Wallet, X } from "lucide-react";
import KpiTile from "@/components/ui/kpi-tile";
import AnimatedTabs from "@/components/ui/animated-tabs";
import { Spotlight } from "@/components/ui/spotlight";
import { BorderBeamPanel } from "@/components/ui/border-beam-panel";
import { toast } from "@/components/ui/toast";
import { useAnimated } from "@/effects";
import { ApiError, getJson } from "@/lib/api";
import { load, peek } from "@/lib/prefetch";
import { useNav } from "@/lib/nav";
import { perfMemory, perfQuery, perfUrl } from "./query";
import { BusyBar } from "@/components/ui/busy-bar";
import { useFocusAccount } from "@/lib/focus";
import { money, pnlClass, rr } from "@/lib/format";
import { cn } from "@/lib/utils";
import { AutoHeight, EmptyState, FooterPage, Reveal, Skeleton } from "@/shell/page";
import { EquityChart, LiveVsExpected } from "./charts";
import { PnlCalendar, loadCalendar } from "./PnlCalendar";
import { Menu } from "./Menu";
import { RiskTab, riskUrl } from "./RiskTabs";
import { AccountBreakdown } from "./AccountBreakdown";
import { StatsBox, readVariant } from "./summaries";
import { TradesTable, tradesUrl } from "./TradesTable";
import type { AccountBrief, Notice, PerfPage, Unit } from "./types";

const TABS = [
  { id: "equity", label: "Equity" }, { id: "drawdown", label: "Drawdown" }, { id: "montecarlo", label: "Monte Carlo" },
  { id: "calendar", label: "Calendar" }, { id: "trades", label: "Trades" },
];
const PHASES: { id: string; label: string }[] = [
  { id: "development", label: "Development (1–6)" }, { id: "walk_forward", label: "Walk-forward 7–9 (totals)" },
  { id: "sealed", label: "Sealed (totals)" }, { id: "demo", label: "Demo" }, { id: "prop", label: "Prop" }, { id: "live", label: "Live" },
];
// sparkline labels carry the year: the ledger runs across years (and demo trading comes after the window)
const MONTH_SHORT = new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" });
/** a source's status as a sentence ("no demo trades yet (from step 24)" → "No demo trades yet (from step 24).") */
const sentence = (s: string | undefined) => (s && s !== "ok" && s !== "synthetic" ? `${s[0].toUpperCase()}${s.slice(1)}.` : null);
const LAST_KEY = "creamflow.lastAccount";
const REAL = new Set(["demo", "prop", "live"]);
// the account menu shows at most this many accounts (plus "All accounts") before it scrolls; past it, a search field (owner 2026-11-16)
const ACCOUNT_MENU_ROWS = 10;
const DAY = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
// a load slower than this shows the thin progress line (owner 2026-11-16); a quicker one shows nothing
const BUSY_AFTER_MS = 300;

/** The account Drawdown and Monte Carlo show: the selected one, or under "All accounts" the last single account selected,
 * else the most recently active one (its latest trade) (owner 2026-11-16). */
function pickRealAccount(d: PerfPage | null, account: string, lastSingle: string | null): { name: string | null; why: "picked" | "last" | "recent" | null } {
  if (account !== "all") return { name: account, why: "picked" };
  const ruled: AccountBrief[] = (d?.accounts ?? []).filter((a) => a.rule);
  if (lastSingle && ruled.some((a) => a.name === lastSingle)) return { name: lastSingle, why: "last" };
  const recent = [...(d?.breakdown?.rows ?? [])].filter((r) => ruled.some((a) => a.name === r.account))
    .sort((x, y) => (x.last_trade < y.last_trade ? 1 : x.last_trade > y.last_trade ? -1 : 0))[0]?.account ?? null;
  const name = recent ?? ruled[0]?.name ?? null;
  return { name, why: name ? "recent" : null };
}
/** "start=…&end=…" of a query (the Drawdown and Monte Carlo date filter) */
const rangeOf = (q: URLSearchParams) => {
  const r = new URLSearchParams();
  if (q.get("start")) r.set("start", q.get("start")!);
  if (q.get("end")) r.set("end", q.get("end")!);
  return r.toString();
};

function Scramble({ text, animated }: { text: string; animated: boolean }) {
  // text scramble on load (after 21st.dev 16620): letters settle left to right in ~0.6 s; Calm shows the text at once
  const [out, setOut] = useState(animated ? "" : text);
  useEffect(() => {
    if (!animated) { setOut(text); return; }
    const glyphs = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/";
    let f = 0;
    const id = setInterval(() => {
      f++;
      setOut(text.split("").map((c, i) => (c === " " || i < f / 1.6 ? c : glyphs[(i * 7 + f * 3) % glyphs.length])).join(""));
      if (f / 1.6 > text.length) { clearInterval(id); setOut(text); }
    }, 16);
    return () => clearInterval(id);
  }, [text, animated]);
  return <span aria-label={text}>{out}</span>;
}

/** The date range: two dates (New York trading dates), cleared with one click. */
function DateRange({ start, end, onChange, animated }: { start: string | null; end: string | null; onChange: (s: string | null, e: string | null) => void; animated: boolean }) {
  const [open, setOpen] = useState(false);
  const [s, setS] = useState(start ?? ""), [e, setE] = useState(end ?? "");
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { setS(start ?? ""); setE(end ?? ""); }, [start, end]);
  useEffect(() => {
    if (!open) return;
    const off = (ev: PointerEvent) => { if (!box.current?.contains(ev.target as Node)) setOpen(false); };
    window.addEventListener("pointerdown", off);
    return () => window.removeEventListener("pointerdown", off);
  }, [open]);
  const text = start || end ? `${start ? DAY.format(new Date(start + "T12:00:00Z")) : "…"} – ${end ? DAY.format(new Date(end + "T12:00:00Z")) : "…"}` : "All dates";
  return (
    <div ref={box} className="relative">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="flex h-8 items-center gap-2 rounded-sm border border-line-strong bg-bg-2 px-2.5 font-mono text-[12px] text-text-1">
        <CalendarRange className="size-3.5 text-text-3" aria-hidden="true" />{text}</button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: animated ? 0.14 : 0 }}
            className="absolute right-0 z-40 mt-1 w-[280px] rounded-md border border-line-strong bg-bg-3 p-3 shadow-[0_12px_32px_rgba(0,0,0,.55)]" data-date-range>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-[12px] text-text-2">From<input type="date" value={s} onChange={(ev) => setS(ev.target.value)} className="mt-1 h-8 w-full rounded-sm border border-line-strong bg-bg-2 px-2 font-mono text-[12px] text-text-1" /></label>
              <label className="text-[12px] text-text-2">To<input type="date" value={e} onChange={(ev) => setE(ev.target.value)} className="mt-1 h-8 w-full rounded-sm border border-line-strong bg-bg-2 px-2 font-mono text-[12px] text-text-1" /></label>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <button type="button" onClick={() => { onChange(null, null); setOpen(false); }} className="h-8 rounded-sm border border-line-strong px-2.5 text-[12.5px] text-text-2 hover:text-text-1">All dates</button>
              <button type="button" onClick={() => { onChange(s || null, e || null); setOpen(false); }} className="h-8 rounded-sm bg-gold px-3 text-[12.5px] font-medium text-[#1a1208]">Apply</button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** What the guard held back, and why (which gate is missing). */
function Notices({ notices }: { notices: Notice[] }) {
  if (!notices.length) return null;
  return (
    <div className="mt-4 flex flex-col gap-1.5" data-guard-notices>
      {notices.map((n) => (
        <p key={n.phase} className="flex items-start gap-2 rounded-sm border border-info/30 bg-info/[.07] px-3 py-2 text-[13px] text-text-1" data-notice={n.phase}>
          <Info className="mt-0.5 size-4 shrink-0 text-info" aria-hidden="true" />
          <span><span className="font-medium">{n.label}: {n.count} trade{n.count === 1 ? "" : "s"} not shown.</span> <span className="text-text-2">{n.reason[0].toUpperCase() + n.reason.slice(1)}.</span>
            {n.gate && <span className="ml-1.5 rounded-full border border-line-strong px-1.5 font-mono text-[10.5px] text-text-2">gate {n.gate} not recorded</span>}</span>
        </p>
      ))}
    </div>
  );
}

export function PerformancePage() {
  const animated = useAnimated();
  const { focus, setFocus } = useFocusAccount();
  const [variant] = useState(readVariant);
  const [tab, setTab] = useState("equity");
  // the Equity panel's two views: the curve, and live vs expected (SPEC §9b item 6; inside Equity so the page keeps the
  // approved five tabs and the account tabs fit beside them at 1440 px)
  const [equityView, setEquityView] = useState<"curve" | "live">("curve");
  const [unit, setUnit] = useState<Unit>("usd");
  const [account, setAccount] = useState<string>(focus ?? "all");
  // each phase on its own (owner 2026-11-15: no "All phases"); Development at first, then the last one shown while the
  // panel is open (leaving and coming back, e.g. the breakdown's Edit and back, keeps it; owner 2026-11-16)
  const [phase, setPhase] = useState(perfMemory.phase);
  useEffect(() => { perfMemory.phase = phase; }, [phase]);
  const [range, setRange] = useState<{ start: string | null; end: string | null }>({ start: null, end: null });
  // the page opens filled (owner 2026-11-16): its data, and for a backtest phase its first plan, were loaded before the
  // page change (query.ts preloadPerformance)
  const [planId, setPlanId] = useState<string | null>(() => peek<PerfPage>(perfUrl(perfQuery({ account: focus ?? "all", phase: perfMemory.phase, planId: null })))?.plans[0]?.id ?? null);
  const [data, setData] = useState<PerfPage | null>(() => peek<PerfPage>(perfUrl(perfQuery({ account: focus ?? "all", phase: perfMemory.phase, planId }))) ?? null);
  // the query the shown data answers: everything below the header follows it, not the menus, so an account switch shows
  // all at once when its data has arrived (owner 2026-11-16)
  const [dataQuery, setDataQuery] = useState<string | null>(() => (data ? perfQuery({ account: focus ?? "all", phase: perfMemory.phase, planId }) : null));
  const [refused, setRefused] = useState<{ reason: string; gate: string | null } | null>(null);
  const realMode = phase === "demo" || phase === "prop" || phase === "live";
  const backtestMode = phase === "development" || phase === "walk_forward" || phase === "sealed";
  // the account filter and the top-bar focus are one selection (real-trades phases have no "All accounts")
  useEffect(() => { if (focus) setAccount(focus); }, [focus]);
  const pickAccount = (name: string) => { setAccount(name); setFocus(name); };
  // the breakdown's Edit (owner 2026-11-16): straight to the account's editor (filled, never through the accounts list),
  // whose back button returns here
  const nav = useNav();
  const editAccount = (name: string) => nav.open(`/accounts/${encodeURIComponent(name)}`, { back: "/performance" });
  // the last single account selected (the page's filter, the top bar or a breakdown row): what Drawdown and Monte Carlo
  // show under "All accounts" (owner 2026-11-16); kept per browser
  const [lastSingle, setLastSingle] = useState<string | null>(() => { try { return localStorage.getItem(LAST_KEY); } catch { return null; } });
  useEffect(() => {
    if (account === "all") return;
    setLastSingle(account);
    try { localStorage.setItem(LAST_KEY, account); } catch { /* storage blocked */ }
  }, [account]);
  const query = useMemo(() => perfQuery({ account, phase, planId, start: range.start, end: range.end }), [account, phase, range, planId]);
  const [horizon, setHorizon] = useState(60);
  // the Monte Carlo plan comparison and the calendar's month live here, so a switch can load the open tab ahead
  const [compare, setCompare] = useState(false);
  const [calMonth, setCalMonth] = useState<{ phase: string; ym: string | null }>({ phase: "", ym: null });
  const [loading, setLoading] = useState(false);
  // the account Drawdown and Monte Carlo show, decided with the data it goes with (so picking an account never changes
  // them before the rest of the page follows)
  const [riskPick, setRiskPick] = useState<ReturnType<typeof pickRealAccount>>(() => {
    if (!data) return { name: null, why: null };
    let last: string | null = null;
    try { last = localStorage.getItem(LAST_KEY); } catch { /* storage blocked */ }
    return pickRealAccount(data, data.selection.account, last);
  });
  // A switch (account, phase, plan, dates) keeps the current page on screen until everything it needs has arrived: the
  // page's data and the open tab's own data. Then all of it shows in one step: the KPI numbers roll, the tab's content
  // crossfades, sections that come or go (the breakdown) open or close with the height glide (owner 2026-11-16). No
  // placeholder in between; a load slower than BUSY_AFTER_MS shows a thin progress line.
  useEffect(() => {
    const ctl = new AbortController();
    setLoading(true);
    (async () => {
      // the open tab's data loads alongside the page's (the query says which account and phase); only Drawdown and Monte
      // Carlo under "All accounts" wait for the page's answer, which says which account they show
      const qp = new URLSearchParams(query);
      const ph = qp.get("phase") ?? phase, isReal = REAL.has(ph), acct = qp.get("account") ?? "all", dPlan = qp.get("plan");
      const risk = tab === "drawdown" || tab === "montecarlo" ? tab : null;
      const riskFor = (name: string | null) => (risk ? riskUrl({ tab: risk, realMode: isReal, account: isReal ? name : null, range: rangeOf(qp), planId: dPlan, horizon, compare }) : null);
      const ahead: Promise<unknown>[] = [];
      // never left unhandled (a switch away aborts them)
      const add = (x: Promise<unknown>) => { ahead.push(x.catch(() => null)); };
      const early = riskFor(acct);
      if (risk && early && !(isReal && acct === "all")) add(load(early, ctl.signal));
      if (tab === "calendar") add(loadCalendar({ account: isReal ? acct : "all", unit, phase: ph, month: calMonth.phase === ph ? calMonth.ym : null, plan: isReal ? null : dPlan }, ctl.signal));
      if (tab === "trades") add(load(tradesUrl(query), ctl.signal));
      const d = await load<PerfPage>(perfUrl(query), ctl.signal);
      if (risk && isReal && acct === "all") { const u = riskFor(pickRealAccount(d, "all", lastSingle).name); if (u) add(load(u, ctl.signal)); }
      // a tab whose own load fails doesn't hold the page back (the tab then shows its own message)
      await Promise.allSettled(ahead);
      if (ctl.signal.aborted) return;
      setData(d); setDataQuery(query); setRefused(null); setLoading(false); setRiskPick(pickRealAccount(d, d.selection.account, lastSingle));
      if (!planId && d.plans.length) setPlanId(d.plans[0].id);
    })().catch((e) => {
      if (ctl.signal.aborted) return;
      setLoading(false);
      if (e instanceof ApiError && e.status === 403) setRefused({ reason: e.message, gate: (e.body as { gate?: string } | undefined)?.gate ?? null });
    });
    return () => ctl.abort();
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps

  // what the shown data is for (the menus change at once; the page body follows when its data has arrived)
  const viewAccount = data?.selection.account ?? account;
  const viewPhase = data?.selection.phase ?? phase;
  const viewReal = REAL.has(viewPhase);
  const viewQuery = useMemo(() => new URLSearchParams(dataQuery ?? query), [dataQuery, query]);
  const viewPlan = viewQuery.get("plan") ?? planId;
  const viewRange = rangeOf(viewQuery);
  // the entry stagger runs once, when the page first fills
  const settled = useRef(data !== null);
  useEffect(() => { if (data) settled.current = true; }, [data]);
  // the Drawdown and Monte Carlo tabs (owner 2026-11-15): real-trades phases (demo, prop, live) show the account in the
  // filter (= the top-bar focus); every other phase runs the strategy's backtest days on the plan in the header; under
  // "All accounts" they stay per account (the last single account selected, else the most recently active one), with a note
  const real = riskPick;
  const realAccount = real.name;
  const realNote = viewReal && viewAccount === "all" && realAccount
    ? `All accounts: Drawdown and Monte Carlo show one account at a time · showing ${realAccount} (${real.why === "last" ? "the last account you selected" : "the most recently active account"}) · pick another in the account menu or the table`
    : null;
  const plan = data?.plans.find((x) => x.id === planId) ?? null;
  const calMonthShown = calMonth.phase === viewPhase ? calMonth.ym : null;

  const exportCsv = async () => {
    const res = await fetch(`/api/performance/export.csv?${query}`, { headers: { accept: "text/csv" } });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      toast("Export refused", { description: body.error, kind: "warn" });
      return;
    }
    const blob = await res.blob();
    const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? "trades.csv";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    const held = JSON.parse(res.headers.get("x-cf-held") ?? "{}") as Record<string, number>;
    const heldN = Object.values(held).reduce((x, y) => x + y, 0);
    toast(`Exported ${name}`, { description: heldN ? `${heldN} trades held back by the ledger guard (walk-forward / sealed months).` : undefined, kind: "ok" });
  };

  const stagger = { hidden: {}, show: { transition: { staggerChildren: animated ? 0.05 : 0 } } };
  const item: Variants = { hidden: animated && !settled.current ? { opacity: 0, y: 8 } : { opacity: 1 }, show: { opacity: 1, y: 0, transition: { duration: animated ? 0.32 : 0, ease: [0.2, 0, 0, 1] as const } } };

  // active accounts first, then the archived ones (passed, failed, closed), all under "All accounts"
  const accs = data?.accounts ?? [];
  const nArchived = accs.filter((a) => a.archived).length;
  const accountOpts = [{ id: "all", name: "All accounts", sub: `${accs.length - nArchived} active · ${nArchived} archived` },
    ...[...accs.filter((a) => !a.archived), ...accs.filter((a) => a.archived)].map((a) => ({ id: a.name, name: a.name,
      group: a.archived ? "Archived" : undefined,
      sub: [a.kind, a.phase, a.draft ? "draft" : null, a.archived ? a.status : null].filter(Boolean).join(" · ") }))];
  const planOpts = (data?.plans ?? []).map((x) => ({ id: x.id, name: x.label, sub: [x.evaluation ? "evaluation" : null, x.funded ? "funded" : null].filter(Boolean).join(" → ") + (x.builtin ? "" : " · saved preset") }));
  const stepPlan = (d: number) => { const ps = data?.plans ?? []; if (!ps.length) return; const i = ps.findIndex((x) => x.id === planId); setPlanId(ps[(i + d + ps.length) % ps.length].id); };
  // KPI sparklines: one point per trading day (owner 2026-11-15); the badge compares the last days with the earlier ones
  const kt = data?.kpi_trend;
  const labels = (kt?.dates ?? []).map((d) => DAY.format(new Date(d + "T12:00:00Z")));
  const num = (a: (number | null)[] | undefined, k = 1) => (a ?? []).map((v) => (v === null ? Number.NaN : v * k));
  const cmp = kt?.compare ?? null;
  const ch = (pick: (c: NonNullable<typeof cmp>["recent"]) => number | null) => {
    if (!cmp) return undefined;
    const a = pick(cmp.recent), b = pick(cmp.earlier);
    return a === null || b === null || !b ? undefined : ((a - b) / Math.abs(b)) * 100;
  };
  const S = data?.kpis;
  const series = {
    net: num(kt?.series.net_usd), netR: num(kt?.series.net_r), tr: num(kt?.series.trades), rpt: num(kt?.series.r_per_trade), upt: num(kt?.series.usd_per_trade),
    wr: num(kt?.series.win_rate, 100), dd: num(kt?.series.dd_usd),
    pf: num(kt?.series.profit_factor),
  };
  const deltaLabel = cmp ? `last ${cmp.days} trading day${cmp.days === 1 ? "" : "s"} vs earlier` : undefined;
  const noData = data && !data.synthetic && data.kpis.trades === 0;
  const ledgerLabel = data?.synthetic ? "SYNTHETIC DATA" : "BACKTEST + LIVE";
  const runOf = (name: string | null) => data?.drawdown.find((r) => r.account === name) ?? null;
  const briefOf = (name: string | null) => data?.accounts.find((a) => a.name === name);

  return (
    <FooterPage ready={data !== null || refused !== null}>
      <motion.div variants={stagger} initial="hidden" animate="show" className="mx-auto max-w-[1680px] px-6 pb-0 pt-7 min-[1440px]:px-8" data-page="performance">
        <motion.div variants={item} className="flex flex-wrap items-end gap-4">
          <div className="min-w-0">
            <div className="eyebrow"><Scramble text="CREAMFLOW / PERFORMANCE" animated={animated} /></div>
            <h1 className="mt-1.5 font-display text-[38px] leading-[40px] tracking-[-0.01em] text-text-1">Performance<br />
              <span className="italic text-cream">{data?.synthetic ? "Synthetic ledger" : noData ? "No trades yet" : "Backtest and live"}</span></h1>
            <p className="mt-1.5 text-[15px] text-text-2">Net of commissions, New York trading dates. Walk-forward months show totals only.</p>
          </div>
          {data?.synthetic && <span className="mb-1 rounded-full border border-warn/40 bg-warn/10 px-2.5 py-0.5 font-mono text-[10.5px] tracking-[0.08em] text-warn" data-synthetic-chip>SYNTHETIC DATA</span>}
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <div className="relative flex w-[212px] justify-end" data-filter-slot>
              <AnimatePresence initial={false} mode="popLayout">
                <motion.div key={realMode ? "account" : "plan"} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: animated ? 0.2 : 0 }}>
                  {!realMode && planId && planOpts.length > 0 && (
                    <Menu value={planId} options={planOpts} onChange={setPlanId} onStep={stepPlan} maxVisible={5} width={212} icon={<Route className="size-3.5 text-text-3" aria-hidden="true" />} label="Plan" animated={animated} />
                  )}
                  {realMode && <Menu value={account} options={accountOpts} onChange={pickAccount} width={212} icon={<Wallet className="size-3.5 text-text-3" aria-hidden="true" />} label="Account" animated={animated}
                    maxVisible={ACCOUNT_MENU_ROWS + 1} searchable={accs.length > ACCOUNT_MENU_ROWS} />}
                </motion.div>
              </AnimatePresence>
            </div>
            <Menu value={phase} options={PHASES.map((p) => ({ id: p.id, name: p.label }))} onChange={setPhase} width={196} icon={<Layers className="size-3.5 text-text-3" aria-hidden="true" />} label="Phase" animated={animated} />
            <DateRange start={range.start} end={range.end} onChange={(s, e) => setRange({ start: s, end: e })} animated={animated} />
            <AnimatedTabs variant="segment" layoutId="unit" animated={animated} activeTab={unit} onChange={(v) => setUnit(v as Unit)} tabs={[{ id: "usd", label: "$" }, { id: "r", label: "R" }]} className="h-8 w-[84px]" />
            <button type="button" onClick={exportCsv} className="flex h-8 items-center gap-2 rounded-sm bg-gold px-3 text-[13px] font-medium text-[#1a1208] transition-transform duration-150 hover:brightness-105 active:scale-[.98]" data-export-csv>
              <Download className="size-3.5" aria-hidden="true" />Export CSV</button>
          </div>
        </motion.div>

        {refused && (
          <motion.div variants={item} className="mt-5 flex items-start gap-3 rounded-md border border-warn/40 bg-warn/[.07] p-4" role="alert" data-refused>
            <Info className="mt-0.5 size-5 shrink-0 text-warn" aria-hidden="true" />
            <div className="flex-1"><div className="font-medium text-text-1">The ledger guard refused this selection.</div>
              <p className="m-0 mt-1 text-[13.5px] text-text-2">{refused.reason}{refused.gate ? ` (gate ${refused.gate} isn't recorded yet).` : "."}</p></div>
            <button type="button" onClick={() => setPhase("development")} className="flex h-8 items-center gap-1.5 rounded-sm border border-line-strong px-2.5 text-[12.5px] text-text-1"><X className="size-3.5" aria-hidden="true" />Development</button>
          </motion.div>
        )}

        {!data && !refused && <motion.div variants={item} className="mt-6 grid grid-cols-2 gap-3 min-[1400px]:grid-cols-6">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-[132px]" />)}</motion.div>}

        {/* a load slower than BUSY_AFTER_MS: a thin line between the header and the page (it never moves the layout) */}
        {data && <div className="relative h-0"><BusyBar busy={loading} delay={BUSY_AFTER_MS} animated={animated} label="Loading the selection" className="top-2.5" data-perf-loading /></div>}

        {data && !refused && (
          <div className="relative" data-page-body={`acct:${viewAccount}`} aria-busy={loading}>
            <motion.div variants={item}><Notices notices={data.notices} /></motion.div>
            {noData && (
              <motion.div variants={item} className="mt-5">
                <EmptyState word="no data" message={<>The page fills in as each phase exists: {[data.sources.backtest, data.sources.demo, data.sources.prop].filter(Boolean).join("; ")}. (<code className="font-mono text-[12.5px]">nqbot ui --synthetic</code> shows a synthetic ledger to look at.)</>} />
              </motion.div>
            )}
            <motion.div variants={item} className="mt-5 grid grid-cols-2 gap-3 min-[1200px]:grid-cols-3 min-[1400px]:grid-cols-6" data-kpis>
              {S && [
                { label: "Net P&L", value: unit === "usd" ? S.net_usd : S.net_r, fmt: (v: number) => (unit === "usd" ? money(v) : rr(v)), trend: unit === "usd" ? series.net : series.netR,
                  delta: ch((c) => (unit === "usd" ? c.net_per_day_usd : c.net_per_day_r)), hint: "Net P&L to date by trading day; the badge compares the net per day" },
                { label: "Trades", value: S.trades, fmt: (v: number) => Math.round(v).toString(), trend: series.tr, delta: ch((c) => c.trades_per_day), hint: "Trades on each trading day; the badge compares the trades per day" },
                // net per trade in the page's unit (owner 2026-11-15): "Net $ per trade" in $, "Net R per trade" in R
                unit === "usd"
                  ? { label: "Net $ per trade", value: S.trades ? S.net_usd / S.trades : 0, fmt: (v: number) => (S.trades ? money(v) : "—"), trend: series.upt, delta: ch((c) => c.usd_per_trade) }
                  : { label: "Net R per trade", value: S.r_per_trade ?? 0, fmt: (v: number) => (S.trades ? rr(v) : "—"), trend: series.rpt, delta: ch((c) => c.r_per_trade) },
                { label: "Win rate", value: (S.win_rate ?? 0) * 100, fmt: (v: number) => (S.win_rate === null ? "—" : `${v.toFixed(1)}%`), trend: series.wr, delta: ch((c) => c.win_rate) },
                { label: "Profit factor", value: S.profit_factor ?? 0, fmt: (v: number) => (S.profit_factor === null ? "—" : v.toFixed(2)), trend: series.pf, delta: ch((c) => c.profit_factor) },
                { label: "Max drawdown", value: S.max_dd_close_usd, fmt: (v: number) => money(v), trend: series.dd, delta: ch((c) => -c.max_dd_usd), lower: true,
                  hint: "The deepest fall from the high, close to close; the line is the drawdown from the high to date" },
              ].map((k) => (
                <Spotlight key={k.label} enabled={animated} className="rounded-md" data-kpi={k.label}>
                  <KpiTile label={k.label} value={k.value} format={k.fmt} trend={k.trend} trendLabels={labels} delta={k.trend.filter(Number.isFinite).length > 1 ? k.delta : undefined}
                    deltaLabel={deltaLabel} lowerIsBetter={k.lower} animated={animated} entrance={false} className="h-full min-h-[200px] bg-bg-2/90" />
                </Spotlight>
              ))}
            </motion.div>
            <motion.div variants={item}><Reveal show={!!S && S.trades > 0}>{S && <StatsBox variant={variant} s={S} unitR={unit === "r"} animated={animated} />}</Reveal></motion.div>
            {/* "All accounts": the per-account table opens and closes with the height glide (owner 2026-11-16) */}
            <motion.div variants={item}><Reveal show={!!data.breakdown} data-breakdown-reveal="">{data.breakdown && <AccountBreakdown data={data.breakdown} focus={null} onPick={pickAccount}
              editable={(n) => !!data.accounts.find((a) => a.name === n)?.editable} onEdit={editAccount} />}</Reveal></motion.div>
            {Object.keys(data.per_phase).length > 1 && (
              <motion.div variants={item} className="mt-3 flex flex-wrap gap-2 font-mono text-[11.5px] text-text-2" data-per-phase>
                {Object.entries(data.per_phase).map(([ph, st]) => (
                  <span key={ph} className="rounded-xs border border-line-strong px-2 py-1">{st.label}: {st.trades} trades · <span className={pnlClass(st.net_usd)}>{money(st.net_usd)}</span> · {st.r_per_trade === null ? "—" : rr(st.r_per_trade)}/trade</span>
                ))}
              </motion.div>
            )}

            <motion.div variants={item} className="mt-7">
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                <AnimatedTabs variant="underline" layoutId="perf-tabs" animated={animated} activeTab={tab} onChange={setTab} tabs={TABS} ariaLabel="Performance views" />
                <div className="flex items-center gap-2 font-mono text-[11px] text-text-3 max-[1199px]:w-full" data-scope-chips>
                  <span className="whitespace-nowrap rounded-xs border border-line-strong px-2 py-0.5" data-scope-chip>NQ · {viewReal ? viewAccount : (plan?.label ?? "strategy")}</span>
                  <span className="whitespace-nowrap rounded-xs border border-line-strong px-2 py-0.5">{viewReal ? "real trades" : "strategy backtest"}</span>
                  <span className="whitespace-nowrap rounded-xs border border-line-strong px-2 py-0.5">Eastern time</span>
                  <span className="whitespace-nowrap rounded-xs border border-line-strong px-2 py-0.5">read only</span>
                </div>
              </div>
              <BorderBeamPanel reducedMotion={!animated} glow={false} className="mt-4 p-4" radius={8}>
                {/* the tab area's height follows its content smoothly, so the footer glides instead of snapping */}
                <AutoHeight>
                <AnimatePresence initial={false} mode="popLayout">
                <motion.div key={tab === "drawdown" || tab === "montecarlo" ? tab : `${tab}:${viewPhase}:${viewReal ? viewAccount : ""}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                  transition={{ duration: animated ? 0.2 : 0, ease: "easeOut" }} data-panel-content>
                {tab === "equity" && (
                  <div className="relative">
                    <div className="absolute right-0 top-0 z-20">
                      <AnimatedTabs variant="segment" layoutId="equity-view" animated={animated} activeTab={equityView} onChange={(v) => setEquityView(v as "curve" | "live")}
                        tabs={[{ id: "curve", label: "Equity curve" }, { id: "live", label: "Live vs expected" }]} className="h-8" ariaLabel="Equity view" />
                    </div>
                    {equityView === "curve" ? (
                      <div className="h-[380px]">
                        {data.equity.points.length ? <EquityChart points={data.equity.points} starts={data.equity.phase_starts} unit={unit} animated={animated} perTrade={data.equity.per_trade} />
                          : <p className="grid h-full place-items-center text-[13.5px] text-text-2">No day-level trades in this selection.</p>}
                      </div>
                    ) : (
                      <div className="h-[380px] pt-10">
                        <LiveVsExpected live={data.live_vs_expected.live_r} band={data.live_vs_expected.band}
                          empty={<p className="grid h-full place-items-center text-center text-[13.5px] text-text-2">{sentence(data.sources.demo) ?? "No demo or live trades in this selection."} The live curve is drawn over the band the backtest predicts for the same number of trades.</p>} />
                      </div>
                    )}
                  </div>
                )}
                {(tab === "drawdown" || tab === "montecarlo") && (
                  <div className="min-h-[440px]">
                    <RiskTab tab={tab} realMode={viewReal} account={realAccount} note={realNote} range={viewRange}
                      planId={viewPlan} onPlan={setPlanId} horizon={horizon} onHorizon={setHorizon} compare={compare} onCompare={setCompare} animated={animated} variant={variant} />
                  </div>
                )}
                {tab === "calendar" && <PnlCalendar account={viewReal ? viewAccount : "all"} phase={viewPhase} plan={viewReal ? null : viewPlan} unit={unit} animated={animated} label={ledgerLabel}
                  month={calMonthShown} onMonth={(ym) => setCalMonth({ phase: viewPhase, ym })} />}
                {tab === "trades" && <TradesTable query={dataQuery ?? query} label={data.synthetic ? "synthetic" : "backtest and live"} />}
                </motion.div>
                </AnimatePresence>
                </AutoHeight>
              </BorderBeamPanel>
            </motion.div>
          </div>
        )}
      </motion.div>
    </FooterPage>
  );
}
