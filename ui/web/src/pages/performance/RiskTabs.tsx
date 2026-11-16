// The Drawdown and Monte Carlo tabs (SPEC §9b item 6; owner 2026-11-15), two modes chosen by the page's phase, one
// layout (rework 4): a chart header with the horizon switch (30 / 60 / 120 trading days, default 60), the headline
// numbers as small stats, a legend, the lifecycle chart (zoom, pan, markers inside).
// - Strategy (backtest phases): the strategy's guarded backtest days on the plan picked in the page header, through its
//   lifecycle with payouts (src/nqbot/performance/lifecycle.py). Monte Carlo: 1,000 paths, the summary (evaluations
//   used, pass rate, days to pass, payouts and their total, fees, net cash), the fan of the balance, "Compare plans".
//   Drawdown: the first N backtest days replayed through the same lifecycle with its totals.
// - My account (real-trades phases: demo, prop, live): the account in the page's filter (= the top-bar focus), only its
//   own stage from where it stands today (src/nqbot/performance/account_mc.py). Evaluation: the chance of passing
//   before failing and the days to each; funded: the chance of the next payout before the floor, the days to it, the
//   payouts over the horizon. Drawdown: its own last N trading days with its floor and its stage's line.
// The content crossfades (200 ms; Calm: instant) when a new selection's data arrives; the old view stays until then.
import { useEffect, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Table2 } from "lucide-react";
import AnimatedTabs from "@/components/ui/animated-tabs";
import { getJson } from "@/lib/api";
import { money, money0, pct } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/shell/page";
import { ChartHeader, Legend, LifecycleChart, type LifeSeries, type Marker, type Swatch } from "./lifecycle";
import { AccountPanels, PlanPanels } from "./mcpanels";
import { ComparePlans, FeesMissing, ReplayStrip, StatGrid, type StatCell, type Variant } from "./summaries";
import type { AcctFan, DdAccount, DdPlans, LifeEvent, McAccount, McPlans, PlanBrief, Range } from "./types";

const fmtDate = (d: string) => new Date(d + "T12:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const fmtShort = (d: string) => new Date(d + "T12:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const p = (v: number | null | undefined) => (v === null || v === undefined ? "—" : pct(v));
const days = (r: Range | null | undefined) => (r && r.median !== null ? `${r.median} day${r.median === 1 ? "" : "s"}` : "—");
const rng = (r: Range | null | undefined, f: (v: number) => string = (v) => String(v)) => (r && r.lo !== null && r.hi !== null ? `10–90%: ${f(r.lo)} – ${f(r.hi)}` : "");

/** The plan's rules in words, for the header's info hint. */
function planHint(pl: PlanBrief) {
  const po = pl.payout;
  return [
    pl.evaluation && pl.evaluation.target_balance !== null ? `Evaluation: ${pl.evaluation.rule}, target ${money(pl.evaluation.target_balance - pl.evaluation.start_usd, false)}${pl.min_trading_days ? `, at least ${pl.min_trading_days} trading days` : ""}.` : null,
    pl.funded ? `Funded: ${pl.funded.rule}.` : null,
    po ? `Payouts from a ${money(po.threshold_usd, false)} balance (${money(po.need_usd, false)} withdrawable${po.max_usd ? `, at most ${money(po.max_usd, false)}` : ""})${po.min_days ? `, ${po.min_days} days a cycle` : ""}${po.wait_calendar_days ? `, ${po.wait_calendar_days} calendar days apart` : ""}${po.profit_days_min ? `, ${po.profit_days_min} days of ${money(po.profit_day_min_usd ?? 0, false)}+` : ""}${po.consistency_pct ? `, best day at most ${Math.round(po.consistency_pct * 100)}% of the cycle` : ""}.` : null,
    "Dollars: each stage's risk per trade, commissions and daily caps on the strategy's R. Not modelled: scaling tiers, the consistency dip rule.",
  ].filter(Boolean).join(" ");
}

function markersOf(events: LifeEvent[], offset: number): Marker[] {
  return events.map((e) => ({ at: e.day - offset, event: e }));
}

function legendFor(series: LifeSeries, events: LifeEvent[], fan: boolean): Swatch[] {
  const kinds = new Set(events.map((e) => e.kind));
  return [
    ...(fan ? (["band-outer", "band-inner", "median", "base"] as Swatch[]) : (["balance"] as Swatch[])),
    fan ? "floor-base" : "floor",
    ...(series.target.some((v) => v !== null) ? (["target"] as Swatch[]) : []),
    ...(series.payout_line.some((v) => v !== null) ? (["payout"] as Swatch[]) : []),
    ...(kinds.has("passed") ? (["passed"] as Swatch[]) : []), ...(kinds.has("payout") ? (["payout-marker"] as Swatch[]) : []),
    ...(kinds.has("failed") ? (["failed"] as Swatch[]) : []),
  ];
}

function Horizon({ value, options, onChange, animated, id }: { value: number; options: number[]; onChange: (h: number) => void; animated: boolean; id: string }) {
  return <AnimatedTabs variant="segment" layoutId={id} animated={animated} activeTab={String(value)} onChange={(v) => onChange(Number(v))}
    tabs={options.map((h) => ({ id: String(h), label: `${h} days` }))} className="h-8" ariaLabel="Trading days" />;
}

type Data = McPlans | McAccount | DdPlans | DdAccount;

export function RiskTab({ tab, realMode, account, note = null, range, planId, horizon, onHorizon, onPlan, animated, variant }: {
  tab: "drawdown" | "montecarlo"; realMode: boolean; account: string | null; note?: string | null;
  range: string; planId: string | null; horizon: number; onHorizon: (h: number) => void; onPlan: (id: string) => void; animated: boolean; variant: Variant;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [compare, setCompare] = useState(false);
  const url = realMode
    ? `/api/performance/${tab}?mode=account&account=${encodeURIComponent(account ?? "")}&horizon=${horizon}`
    : `/api/performance/${tab}?mode=plans${planId ? `&plan=${encodeURIComponent(planId)}` : ""}&horizon=${horizon}${tab === "montecarlo" && compare ? "&compare=1" : ""}${range ? `&${range}` : ""}`;
  useEffect(() => {
    const ctl = new AbortController();
    if (realMode && !account) { setErr("No account with a drawdown rule to show."); return; }
    getJson<Data>(url, ctl.signal).then((d) => { setData(d); setErr(null); })
      .catch((e) => { if (!ctl.signal.aborted) setErr(String(e?.message ?? e)); });
    return () => ctl.abort();
  }, [url]); // eslint-disable-line react-hooks/exhaustive-deps
  // the old view stays until the new data has arrived; a response of the other tab's shape is never drawn
  const fits = !!data && (tab === "drawdown" ? "replay" in data || "run" in data : "fan" in data || "state" in data);
  let key = "loading", body: ReactNode = <Skeleton className="h-[560px]" />;
  if (err) { key = `err:${err}`; body = <p className="grid h-[560px] place-items-center text-center text-[13.5px] text-text-2">{err}</p>; }
  else if (data && fits) {
    key = data.mode === "plans" ? `plans:${tab}:${data.selected}` : `account:${tab}:${data.account.name}`;
    body = data.mode === "plans"
      ? (tab === "drawdown" ? <PlanDrawdown d={data as DdPlans} onHorizon={onHorizon} animated={animated} />
        : <PlanMonteCarlo m={data as McPlans} onHorizon={onHorizon} onPlan={onPlan} animated={animated} variant={variant} compare={compare} setCompare={setCompare} />)
      : (tab === "drawdown" ? <AccountDrawdown d={data as DdAccount} onHorizon={onHorizon} animated={animated} />
        : <AccountMonteCarlo m={data as McAccount} onHorizon={onHorizon} animated={animated} variant={variant} />);
  }
  return (
    <div className="relative min-h-[560px]" data-risk-tab={tab}>
      {note && <p className="mb-2 rounded-sm border border-info/30 bg-info/[.07] px-3 py-1.5 font-mono text-[11.5px] text-text-2" data-account-note>{note}</p>}
      <AnimatePresence initial={false} mode="popLayout">
        <motion.div key={key} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: animated ? 0.2 : 0, ease: "easeOut" }}>
          {body}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

function PlanDrawdown({ d, onHorizon, animated }: { d: DdPlans; onHorizon: (h: number) => void; animated: boolean }) {
  const sel = d.plans.find((x) => x.id === d.selected) ?? d.plans[0];
  const r = d.replay;
  return (
    <div className="flex flex-col gap-3" data-risk-mode="plans">
      <ChartHeader kicker="Drawdown · backtest replay" hint={`${d.source}. ${planHint(sel)}`}
        title={<>{sel.label} <span className="italic text-cream">through its lifecycle</span></>}
        right={<Horizon value={d.horizon} options={d.horizons} onChange={onHorizon} animated={animated} id="dd-horizon" />} />
      {!r.dates.length || !r.totals ? <p className="grid h-[440px] place-items-center text-[13.5px] text-text-2">No backtest days in this selection.</p> : <>
        <ReplayStrip t={r.totals} end={fmtDate(r.dates[r.dates.length - 1])} />
        <Legend items={legendFor(r, r.events, false)} />
        <LifecycleChart series={r} markers={markersOf(r.events, 1)} xTick={(i) => fmtShort(r.dates[i])} xLabel={(i) => fmtDate(r.dates[i])}
          animated={false} splitPct={sel.payout?.split_pct ?? null} height={380} />
      </>}
    </div>
  );
}

function PlanMonteCarlo({ m, onHorizon, onPlan, animated, variant, compare, setCompare }: {
  m: McPlans; onHorizon: (h: number) => void; onPlan: (id: string) => void; animated: boolean; variant: Variant; compare: boolean; setCompare: (f: (v: boolean) => boolean) => void;
}) {
  const sel = m.plans.find((x) => x.id === m.selected) ?? m.plans[0];
  const f = m.fan;
  return (
    <div className="flex flex-col gap-3" data-risk-mode="plans">
      <ChartHeader kicker={`Monte Carlo · ${f.n.toLocaleString()} paths`}
        hint={`${m.source}. ${planHint(sel)} Paths: whole backtest days drawn with replacement; every path starts as the evaluation and runs the full lifecycle until day ${m.horizon}.`}
        title={<>{sel.label} <span className="italic text-cream">over {m.horizon} trading days</span></>}
        right={<>
          <Horizon value={m.horizon} options={m.horizons} onChange={onHorizon} animated={animated} id="mc-horizon" />
          <button type="button" onClick={() => setCompare((v) => !v)} aria-pressed={compare} data-compare-toggle
            className={cn("flex h-8 items-center gap-1.5 rounded-sm border px-2.5 text-[12.5px]", compare ? "border-gold/60 bg-gold/10 text-text-1" : "border-line-strong text-text-2 hover:text-text-1")}>
            <Table2 className="size-3.5" aria-hidden="true" />Compare plans</button>
        </>} />
      {f.numbers && <PlanPanels variant={variant} n={f.numbers} horizon={m.horizon} luck={f.order_luck} animated={animated} feesChip={<FeesMissing missing={f.numbers.fees_missing} />} />}
      {compare && m.compare && <ComparePlans rows={m.compare} selected={m.selected} onPick={onPlan} horizon={m.horizon} />}
      <Legend items={legendFor(f.base, f.base.events, true)} />
      <LifecycleChart key={m.horizon} series={f.base} markers={markersOf(f.base.events, 0)} bands={f.bands} extras={{ median: f.bands.map((b) => b.p50) }}
        xTick={(i) => (i ? `day ${i}` : "start")} xLabel={(i) => (i ? `Day ${i}` : "Start")} animated={animated} splitPct={sel.payout?.split_pct ?? null}
        height={380} floorLabel="floor (backtest order)" />
    </div>
  );
}

function AccountMonteCarlo({ m, onHorizon, animated, variant }: { m: McAccount; onHorizon: (h: number) => void; animated: boolean; variant: Variant }) {
  const a = m.account;
  const header = (sub: string) => (
    <ChartHeader kicker="Monte Carlo · from today" hint={`Only this account's own stage, from where it stands today: a passed evaluation becomes a new funded account with its own ID, so no path changes stage or starts a new evaluation. Days: the strategy's backtest days in this account's dollars (${m.source}).`}
      title={<>My account · <span className="italic text-cream">{a.name}</span> <span className="text-text-3">{sub}</span></>}
      right={<Horizon value={m.horizon} options={m.horizons} onChange={onHorizon} animated={animated} id="mc-horizon" />} />
  );
  if (!m.state || !m.fan || !m.fan.numbers) {
    return <div className="flex flex-col gap-3" data-risk-mode="account">{header("")}<p className="grid h-[440px] place-items-center text-center text-[13.5px] text-text-2">{a.name}: {m.reason ?? "no backtest days in this selection"}.</p></div>;
  }
  const f = m.fan;
  return (
    <div className="flex flex-col gap-3" data-risk-mode="account">
      {header(f.stage === "evaluation" ? "· evaluation" : "· funded")}
      <AccountPanels f={f} horizon={m.horizon} variant={variant} animated={animated} />
      {f.stage === "funded" && f.start.cycle_from && (
        <p className="-mt-1 font-mono text-[11px] text-text-3" data-cycle-note>
          Payout cycle from {f.start.cycle_from === "today (assumed)" ? "today: assumed until the engine records payouts (step 22); set the last payout date on the Accounts page to place it" : f.start.cycle_from}.</p>
      )}
      <Legend items={legendFor(f.base, f.base.events, true)} />
      <LifecycleChart key={m.horizon} series={f.base} markers={markersOf(f.base.events, 0)} bands={f.bands} extras={{ median: f.bands.map((b) => b.p50) }}
        xTick={(i) => (i ? `day ${i}` : "today")} xLabel={(i) => (i ? `Day ${i} from today` : "Today")} animated={animated} splitPct={f.split_pct}
        height={380} floorLabel="floor (backtest order)" />
    </div>
  );
}

function AccountDrawdown({ d, onHorizon, animated }: { d: DdAccount; onHorizon: (h: number) => void; animated: boolean }) {
  const a = d.account, r = d.run;
  const header = (
    <ChartHeader kicker="Drawdown · real trades" hint={`One point per trade at its exit time, the last ${d.horizon} trading days. ${a.rule ?? ""}`}
      title={<>My account · <span className="italic text-cream">{a.name}</span> <span className="text-text-3">{d.stage ? `· ${d.stage}` : ""}</span></>}
      right={<Horizon value={d.horizon} options={d.horizons} onChange={onHorizon} animated={animated} id="dd-horizon" />} />
  );
  if (!r) return <div className="flex flex-col gap-3" data-risk-mode="account">{header}<p className="grid h-[440px] place-items-center text-center text-[13.5px] text-text-2">{a.name}: {d.reason}.</p></div>;
  const last = r.balance.length - 1;
  const room = r.balance[last] - r.floor_now;
  const roomF = room / Math.max(1, r.drawdown_usd);
  // the room's bar: full = the whole drawdown left; it turns towards red as the room shrinks
  const roomColor = `color-mix(in oklab, var(--color-ok) ${Math.round(Math.max(0, Math.min(1, roomF)) * 100)}%, var(--color-crit))`;
  // "Floor locked" from the account's own plan rules (owner 2026-11-16)
  const lock: StatCell = (() => {
    const base = { key: "locked", label: "Floor locked" } as const;
    if (r.rule_type === "static") return { ...base, value: "Static", caption: "this plan's floor never moves" };
    if (r.lock_usd === null) return { ...base, value: "No lock", caption: "this plan's floor always trails" };
    const L = money0(r.lock_usd);
    const byPayout = r.lock_trigger !== "floor" && r.payouts_taken > 0;
    if (r.locked_at !== null || byPayout || r.floor_now >= r.lock_usd - 0.005) {
      return { ...base, value: "Locked", cls: "text-ok", caption: byPayout && r.locked_at === null ? `at ${L} since the first payout` : `at ${L} since ${fmtShort(r.dates[r.locked_at ?? last])}`, bar: { v: 1, color: "var(--color-ok)" } };
    }
    if (r.lock_trigger === "first_payout") return { ...base, value: "At 1st payout", caption: `then locks at ${L}`, hint: "This plan's floor trails until the first payout, then stays at the lock" };
    const progress = (r.floor_now - r.start_floor) / Math.max(1, r.lock_usd - r.start_floor);
    return { ...base, value: `${money0(r.lock_usd - r.floor_now)} to go`, caption: `locks at ${L}${r.lock_trigger === "floor_or_first_payout" ? " or 1st payout" : ""}`,
      bar: { v: progress, color: "var(--color-gold)" }, hint: "How much higher the floor must trail (the end-of-day high must rise as much) before it locks" };
  })();
  const cells: StatCell[] = [
    { key: "balance", label: "Balance", value: money0(r.balance[last]), caption: `as of ${fmtShort(r.dates[last])} ${r.clocks[last]}` },
    { key: "floor", label: "Floor in force", value: money0(r.floor_now), cls: "text-crit", caption: r.rule_type === "static" ? "static" : `set at the ${fmtShort(r.dates[last])} close` },
    { key: "room", label: "Room to the floor", value: money0(room), caption: `${Math.round(roomF * 100)}% of the ${money0(r.drawdown_usd)} drawdown`,
      bar: { v: roomF, color: roomColor }, hint: "Balance minus the floor in force; the bar is the room left out of the full drawdown" },
    { key: "closest", label: "Closest approach", value: r.closest_room !== null ? money0(r.closest_room) : "—", caption: r.closest_at !== null ? `${fmtShort(r.dates[r.closest_at])} ${r.clocks[r.closest_at]}` : "no trade yet",
      hint: "The least room to the floor: after any trade (the floor in force then) or after a session's close (the floor it set)" },
    lock,
  ];
  const series: LifeSeries = { balance: r.balance, floor: r.floor, target: r.target, payout_line: r.payout_line, stage: r.stage };
  return (
    <div className="flex flex-col gap-3" data-risk-mode="account">
      {header}
      <StatGrid cells={cells} cols={5} data-account-numbers />
      <Legend items={legendFor(series, [], false)} />
      {/* one point per trade at its exit time, from the account's start (owner 2026-11-16); each trading day labelled once */}
      <LifecycleChart series={series} markers={[]} xTick={(i) => fmtShort(r.dates[i])} tickAt={r.day_first}
        xLabel={(i) => `${fmtDate(r.dates[i])} ${r.clocks[i]}${r.pnl[i] === null ? " · start" : ` · trade ${money(r.pnl[i] as number)}`}`} animated={false} splitPct={null} height={380} />
    </div>
  );
}
