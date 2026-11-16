// The Monte Carlo summaries (owner 2026-11-16, redesign): the account's own stage and the strategy's plan, in the same
// cards, two variants (A "gauge" by default, B "bars" with `?variant=b`). Stat cards that light up like the KPI tiles
// (Spotlight 35180), muted labels, prominent numbers; the headline chance as an animated ring (A) or an animated
// partition bar (B); the account's position as one bar from its floor to its target (or its payout line) with today's
// balance on it; every 10–90% range as a small range bar.
// From 21st.dev (code read 2026-11-16; no network calls, nothing embedded): the ring after "Circular Progress with
// Custom Color" (shadcnui-blocks progress-10, id 22095) and "Progress" Vo2 Max card (kavikatiyar, id 7636); the split
// bar after "Partition Bar" (8starlabs, id 26545); the position bar after "Reshaped Progress bar" (reshaped, id 17906:
// a value between min / max boundaries); written in our tokens, no cva, no Radix slider.
import type { ReactNode } from "react";
import { motion } from "motion/react";
import { Spotlight } from "@/components/ui/spotlight";
import { money0, pct } from "@/lib/format";
import { cn } from "@/lib/utils";
import { RangeBar, type Variant } from "./summaries";
import type { AcctFan, PlanNumbers, Range } from "./types";

const EASE = [0.22, 1, 0.36, 1] as const;
const LABEL = "eyebrow truncate text-text-3";
const p = (v: number | null | undefined) => (v === null || v === undefined ? "—" : pct(v));
const rangeText = (r: Range | null | undefined, f: (v: number) => string = (v) => String(v)) =>
  r && r.lo !== null && r.hi !== null ? `10–90%  ${f(r.lo)} – ${f(r.hi)}` : "";

function Card({ children, className, animated, ...rest }: { children: ReactNode; className?: string; animated: boolean } & Record<`data-${string}`, string | undefined>) {
  return (
    <Spotlight enabled={animated} className={cn("min-w-0 rounded-md border border-line-strong/70 bg-bg-2/90 p-4", className)} {...rest} data-summary-card="">
      {children}
    </Spotlight>
  );
}

// ── pieces ──
interface Part { v: number; color: string; label: string }

/** The headline chance as a ring: its parts drawn in turn from the top, the arc filling in (Calm: at once). */
function Ring({ parts, center, sub, animated, size = 124 }: { parts: Part[]; center: string; sub: string; animated: boolean; size?: number }) {
  const stroke = 7, r = (size - stroke) / 2;
  let at = 0;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} data-ring>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-line-strong)" strokeOpacity={0.35} strokeWidth={stroke} />
        {parts.filter((x) => x.v > 0.0005).map((x) => {
          const start = at; at += x.v;
          const len = Math.max(0, x.v - (parts.length > 1 ? 0.008 : 0));
          return (
            <motion.circle key={x.label} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={x.color} strokeWidth={stroke} strokeLinecap="butt"
              pathLength={1} strokeDashoffset={-start}
              initial={animated ? { strokeDasharray: "0 1" } : false} animate={{ strokeDasharray: `${len} 1` }}
              transition={{ duration: animated ? 0.9 : 0, ease: EASE, delay: animated ? start * 0.6 : 0 }} />
          );
        })}
      </svg>
      <div className="absolute inset-0 grid place-content-center text-center">
        <div className="font-mono text-[19px] font-medium leading-none tabular-nums text-text-1" data-ring-value>{center}</div>
        <div className="mt-1.5 font-mono text-[9px] uppercase tracking-[0.05em] text-text-3">{sub}</div>
      </div>
    </div>
  );
}

/** The same parts as one bar with its values under each segment (Partition Bar), filling in from the left. */
function Partition({ parts, animated }: { parts: Part[]; animated: boolean }) {
  const shown = parts.filter((x) => x.v > 0.0005);
  return (
    <div data-partition>
      <div className="flex h-3 gap-1 overflow-hidden rounded-full">
        {shown.map((x, i) => (
          <motion.div key={x.label} className="h-full rounded-full" style={{ background: x.color }}
            initial={animated ? { flexBasis: "0%" } : false} animate={{ flexBasis: `${x.v * 100}%` }}
            transition={{ duration: animated ? 0.8 : 0, ease: EASE, delay: animated ? i * 0.12 : 0 }} />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {parts.map((x) => (
          <div key={x.label} className="flex items-center gap-1.5 font-mono text-[11.5px]">
            <span className="size-2 rounded-full" style={{ background: x.color }} />
            <span className="text-text-3">{x.label}</span><span className="text-text-1">{p(x.v)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Legend({ parts }: { parts: Part[] }) {
  return (
    <div className="grid gap-1">
      {parts.map((x) => (
        <div key={x.label} className="flex items-center gap-2 whitespace-nowrap font-mono text-[12px]" data-legend-row>
          <span className="size-2 shrink-0 rounded-full" style={{ background: x.color }} />
          <span className="text-text-3">{x.label}</span><span className="tabular-nums text-text-1">{p(x.v)}</span>
        </div>
      ))}
    </div>
  );
}

/** Where the account stands: one bar from its floor to its target (or payout line), today's balance on it. */
function Position({ floor, goal, goalLabel, balance, animated }: { floor: number; goal: number; goalLabel: string; balance: number; animated: boolean }) {
  const f = Math.max(0, Math.min(1, (balance - floor) / Math.max(1, goal - floor)));
  return (
    <div data-position>
      <div className="relative h-6">
        <motion.div className="absolute bottom-0 -translate-x-1/2 whitespace-nowrap font-mono text-[11.5px] text-text-1"
          initial={animated ? { left: "0%" } : false} animate={{ left: `${Math.min(88, Math.max(12, f * 100))}%` }} transition={{ duration: animated ? 0.9 : 0, ease: EASE }}>
          today <span className="text-cream">{money0(balance)}</span></motion.div>
      </div>
      <div className="relative mt-1 h-2.5 rounded-full bg-line-strong/40">
        <motion.div className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-crit/70 via-gold/80 to-ok/90"
          initial={animated ? { width: "0%" } : false} animate={{ width: `${f * 100}%` }} transition={{ duration: animated ? 0.9 : 0, ease: EASE }} />
        <motion.div className="absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bg-2 bg-cream shadow-[0_0_0_3px_rgba(255,224,194,.18)]"
          initial={animated ? { left: "0%" } : false} animate={{ left: `${f * 100}%` }} transition={{ duration: animated ? 0.9 : 0, ease: EASE }} />
      </div>
      <div className="mt-2 flex items-start justify-between gap-3 font-mono text-[11.5px]">
        <div><div className="text-text-3">floor</div><div className="text-crit">{money0(floor)}</div></div>
        <div className="text-center text-text-3"><div>{money0(balance - floor)} of room</div><div>{money0(Math.max(0, goal - balance))} to go</div></div>
        <div className="text-right"><div className="text-text-3">{goalLabel}</div><div className="text-ok">{money0(goal)}</div></div>
      </div>
    </div>
  );
}

/** Days traded so far as pips, one per day; the plan's minimum days marked (the pips up to it outlined until done). */
function Pips({ days, min }: { days: number; min: number }) {
  const total = Math.min(20, Math.max(days, min, 1));
  return (
    <div className="flex items-center gap-1.5" data-pips aria-hidden="true">
      {Array.from({ length: total }, (_, i) => (
        <span key={i} className={cn("relative h-3.5 w-2.5 rounded-[2px]", i < days ? "bg-gold" : "border border-line-strong bg-transparent",
          min && i === min - 1 ? "after:absolute after:-right-[4px] after:-top-1 after:h-[22px] after:w-px after:bg-ok" : "")} />
      ))}
      {Math.max(days, min) > 20 && <span className="ml-1 font-mono text-[10.5px] text-text-3">+{Math.max(days, min) - 20}</span>}
    </div>
  );
}

/** A small bar of a value against a limit (risk per trade against the daily cap). */
function Meter({ v, of, animated }: { v: number; of: number; animated: boolean }) {
  const f = Math.max(0, Math.min(1, v / Math.max(1, of)));
  return (
    <div className="relative h-2 rounded-full bg-line-strong/40" data-meter aria-hidden="true">
      <motion.div className="absolute inset-y-0 left-0 rounded-full bg-gold/80" initial={animated ? { width: "0%" } : false}
        animate={{ width: `${f * 100}%` }} transition={{ duration: animated ? 0.8 : 0, ease: EASE }} />
    </div>
  );
}

/** A stat card, one structure for all (label · the value with its visual beside it, centred on it · a footnote), so the
 * values, visuals and footnotes of a row of cards line up. The visual is the 10–90% range bar, or a card's own. */
function Stat({ label, value, r, max, fmt, caption, hint, tone = "gold", cls, visual, animated }: {
  label: string; value: ReactNode; r?: Range | null; max?: number; fmt?: (v: number) => string; caption?: ReactNode; hint?: string;
  tone?: "gold" | "ok" | "crit"; cls?: string; visual?: ReactNode; animated: boolean;
}) {
  return (
    <Card animated={animated} className="flex flex-col" data-metric={label} data-hint={hint}>
      <div className={LABEL}>{label}</div>
      <div className="mt-2 flex h-6 items-center gap-4" data-stat-row>
        <div className={cn("shrink-0 font-mono text-[20px] font-medium leading-none tabular-nums text-text-1", cls)} data-stat-value>{value}</div>
        <div className="min-w-0 flex-1" data-stat-visual>
          {visual ?? (r ? <RangeBar r={r} max={max ?? Math.max(1, (r.hi ?? 0) * 1.1)} min={Math.min(0, r.lo ?? 0)} tone={tone} /> : <div className="h-2" />)}
        </div>
      </div>
      <div className="mt-2 truncate font-mono text-[11px] text-text-3" data-stat-foot>{r ? rangeText(r, fmt) : caption ?? " "}</div>
    </Card>
  );
}

// ── the two layouts ──
interface Head { label: string; parts: Part[]; center: string; sub: string; side?: ReactNode; hint?: string }

function Layout({ variant, head, main, stats, animated, ...rest }: {
  variant: Variant; head: Head; main: ReactNode; stats: ReactNode[]; animated: boolean;
} & Record<`data-${string}`, string | undefined>) {
  if (variant === "b") {
    return (
      <div className="grid gap-3" data-variant="b" {...rest}>
        <Card animated={animated} className="grid gap-5 min-[900px]:grid-cols-2 min-[900px]:gap-8">
          <div data-hint={head.hint}>
            <div className={LABEL}>{head.label}</div>
            <div className="mt-2 flex items-baseline gap-3">
              <span className="font-mono text-[34px] font-medium leading-none tabular-nums text-text-1" data-ring-value>{head.center}</span>
              <span className="font-mono text-[10px] uppercase tracking-[0.05em] text-text-3">{head.sub}</span>
            </div>
            <div className="mt-4"><Partition parts={head.parts} animated={animated} /></div>
            {head.side && <div className="mt-2 font-mono text-[11px] text-text-3">{head.side}</div>}
          </div>
          <div className="min-w-0 border-line-strong/50 min-[900px]:border-l min-[900px]:pl-8">{main}</div>
        </Card>
        <div className="grid grid-cols-2 gap-3 min-[1100px]:grid-cols-4">{stats}</div>
      </div>
    );
  }
  return (
    <div className="grid gap-2.5" data-variant="a" {...rest}>
      <div className="grid gap-2.5 min-[900px]:grid-cols-[minmax(320px,0.85fr)_1.15fr]">
        <Card animated={animated} className="flex items-center gap-5" data-hint={head.hint} data-head-card="">
          <div className="flex min-w-0 flex-1 flex-col self-stretch">
            <div className={LABEL}>{head.label}</div>
            <div className="mt-3 flex flex-1 flex-col justify-center"><Legend parts={head.parts} />{head.side && <div className="mt-2 font-mono text-[11px] text-text-3">{head.side}</div>}</div>
          </div>
          <Ring parts={head.parts} center={head.center} sub={head.sub} animated={animated} />
        </Card>
        <Card animated={animated} className="flex flex-col" data-main-card="">{main}</Card>
      </div>
      <div className="grid grid-cols-2 gap-2.5 min-[1100px]:grid-cols-4">{stats}</div>
    </div>
  );
}

// ── the account's own stage ──
export function AccountPanels({ f, horizon, variant, animated }: { f: AcctFan; horizon: number; variant: Variant; animated: boolean }) {
  const n = f.numbers!, st = f.start, luck = f.order_luck;
  const evaluation = n.stage === "evaluation";
  const goal = evaluation ? f.rules.target_balance : f.payout?.threshold_usd ?? null;
  const head: Head = evaluation
    ? { label: "Pass before failing", center: p(n.p_pass), sub: "pass first", side: luck ? `order luck ${p(luck.p_pass)}` : undefined,
        parts: [{ v: n.p_pass ?? 0, color: "var(--color-ok)", label: "pass first" }, { v: n.p_open ?? 0, color: "var(--color-text-3)", label: `open at day ${horizon}` }, { v: n.p_fail, color: "var(--color-crit)", label: "fail first" }],
        hint: `Of 1,000 paths from today over ${horizon} trading days: the profit target (with the minimum trading days) reached before the floor` }
    : { label: "Next payout before the floor", center: p(n.p_payout), sub: "payout first", side: luck ? `order luck ${p(luck.p_payout)}` : undefined,
        parts: [{ v: n.p_payout ?? 0, color: "var(--color-ok)", label: "payout first" }, { v: Math.max(0, 1 - (n.p_payout ?? 0) - n.p_fail), color: "var(--color-text-3)", label: `neither by day ${horizon}` }, { v: n.p_fail, color: "var(--color-crit)", label: "floor first" }],
        hint: `Of 1,000 paths from today over ${horizon} trading days: a payout allowed by the account's rules before the floor` };
  const main = (
    <>
      <div className={LABEL}>Where it stands today</div>
      <div className="flex flex-1 flex-col justify-center" data-main-body>{goal !== null ? <Position floor={st.floor} goal={goal} goalLabel={evaluation ? "target" : "payout line"} balance={st.balance} animated={animated} />
        : <div className="font-mono text-[22px] text-text-1">{money0(st.balance)}</div>}</div>
    </>
  );
  const days = (r: Range | null | undefined) => (r && r.median !== null ? `${r.median} day${r.median === 1 ? "" : "s"}` : "—");
  const split = f.split_pct !== null && f.split_pct < 1 ? ` · your ${Math.round(f.split_pct * 100)}%` : "";
  const stats = evaluation ? [
    <Stat key="dp" label="Days to pass" value={days(n.days_to_pass)} r={n.days_to_pass} tone="ok" animated={animated} hint="Trading days from today, over the paths that pass" />,
    <Stat key="df" label="Days to fail" value={days(n.days_to_fail)} r={n.days_to_fail} tone="crit" animated={animated} hint="Trading days from today, over the paths that reach the floor" />,
    <Stat key="dt" label="Days traded so far" value={st.days_in} visual={<Pips days={st.days_in} min={f.min_trading_days} />} animated={animated}
      caption={f.min_trading_days ? `minimum ${f.min_trading_days} day${f.min_trading_days === 1 ? "" : "s"} to pass` : "in this evaluation"} />,
    <Stat key="rk" label="Risk per trade" value={money0(f.rules.risk_usd)} animated={animated}
      visual={<Meter v={f.rules.risk_usd} of={f.rules.daily_profit_cap ?? f.rules.drawdown_usd} animated={animated} />}
      caption={f.rules.daily_profit_cap !== null ? `of the ${money0(f.rules.daily_profit_cap)} daily cap` : `of the ${money0(f.rules.drawdown_usd)} drawdown`} />,
  ] : [
    <Stat key="dp" label="Days to next payout" value={days(n.days_to_payout)} r={n.days_to_payout} tone="ok" animated={animated} />,
    <Stat key="pn" label={`Payouts in ${horizon} days`} value={n.payouts_n?.median ?? "—"} r={n.payouts_n} animated={animated} />,
    <Stat key="ps" label={`Payouts to you${split}`} value={money0(n.payouts_share?.median ?? 0)} r={n.payouts_share} fmt={money0} cls="text-pnl-up" animated={animated} />,
    <Stat key="df" label="Days to the floor" value={days(n.days_to_fail)} r={n.days_to_fail} tone="crit" animated={animated} />,
  ];
  return <Layout variant={variant} head={head} main={main} stats={stats} animated={animated} data-account-numbers="" />;
}

// ── the strategy's plan ──
export function PlanPanels({ n, luck, horizon, variant, animated, feesChip }: { n: PlanNumbers; luck: PlanNumbers | null; horizon: number; variant: Variant; animated: boolean; feesChip: ReactNode }) {
  const rate = n.pass_rate?.value ?? 0;
  const head: Head = {
    label: "Pass rate", center: p(n.pass_rate?.value), sub: "of evaluations",
    parts: [{ v: rate, color: "var(--color-ok)", label: "passed" }, { v: 1 - rate, color: "var(--color-crit)", label: "failed" }],
    side: [n.pass_rate && n.pass_rate.lo !== null ? `per path ${p(n.pass_rate.lo)} – ${p(n.pass_rate.hi)}` : null, luck?.pass_rate ? `order luck ${p(luck.pass_rate.value)}` : null].filter(Boolean).join(" · "),
    hint: "Finished evaluations that passed, all paths together; the per-path range below",
  };
  const cash = n.net_cash ?? n.payouts_share;
  const sharePct = n.split_pct !== null && n.split_pct < 1 ? Math.round(n.split_pct * 100) : null;
  const main = (
    <>
      <div className="flex items-center justify-between gap-2" data-main-label>
        <span className={LABEL}>{n.net_cash !== null ? "Net cash" : sharePct ? `Your ${sharePct}% of payouts` : "Payouts to you"} · median</span>{feesChip}
      </div>
      <div className="flex flex-1 flex-col justify-center" data-main-body>
      <div className={cn("font-mono text-[34px] font-medium leading-none tabular-nums", (cash?.median ?? 0) >= 0 ? "text-pnl-up" : "text-pnl-down")} data-hero>
        {cash?.median != null ? money0(cash.median) : "—"}</div>
      <div className="mt-3">
        <RangeBar r={cash ?? { median: null, lo: null, hi: null }} max={Math.max(1, (cash?.hi ?? 0) * 1.05)} min={Math.min(0, cash?.lo ?? 0)} tone="ok" />
        <div className="mt-1.5 flex flex-wrap justify-between gap-x-3 font-mono text-[11px] text-text-3">
          <span>{rangeText(cash, money0)}</span>{luck?.payouts_share && <span>order luck {money0(luck.payouts_share.median ?? 0)}</span>}
        </div>
      </div>
      </div>
    </>
  );
  const stats = [
    <Stat key="ev" label="Evaluations used" value={n.evaluations?.median ?? "—"} r={n.evaluations} animated={animated} hint="The first evaluation plus one after every floor" />,
    <Stat key="dp" label="Days to pass" value={n.days_to_pass?.median != null ? `${n.days_to_pass.median} day${n.days_to_pass.median === 1 ? "" : "s"}` : "—"} r={n.days_to_pass} tone="ok" animated={animated} />,
    <Stat key="pn" label="Payouts taken" value={n.payouts_n?.median ?? "—"} r={n.payouts_n} animated={animated}
      hint={`Gross ${money0(n.payouts_gross?.median ?? 0)} (median); at the maximum each time the rules allow`} />,
    <Stat key="fe" label="Fees paid" value={n.fees ? money0(n.fees.median ?? 0) : "—"} r={n.fees} fmt={money0} tone="crit" caption="not entered" animated={animated} />,
  ];
  return <Layout variant={variant} head={head} main={main} stats={stats} animated={animated} data-plan-numbers="" />;
}
