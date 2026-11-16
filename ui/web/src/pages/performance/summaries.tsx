// The strategy view's number panels (owner 2026-11-15, design pass): the Monte Carlo summary and the stats box, each in
// two variants for the owner to pick (`?variant=b` shows B), the Drawdown replay's totals and the Compare plans table.
// From 21st.dev (code read 2026-11-15; no network calls, nothing embedded; inline <style> blocks moved into classes):
// - "Stats Grid" (shadcnui-blocks stats-04, id 29195): one bordered grid of cells (-m-px borders): stats box A, summary B;
// - "Insight Cards" (arihantcodes, id 29167): a row of small grouped cards, label / mono value / note: stats box B;
// - "Leaderboard Table" (eugeneshilow, id 29468): bars with ±range whiskers and numeric columns, rows that light up on
//   hover: the range bars of summary B and the Compare plans table;
// - the KPI tile (40086, already on the page): summary A's hero cell uses its label / value / caption rhythm.
// Less text: secondary detail sits in `data-hint` tooltips; ranges are the 10–90% of the 1,000 paths.
import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { DeltaNumberTicker } from "@/components/ui/delta-number-ticker";
import { Spotlight } from "@/components/ui/spotlight";
import { money, money0, pct, pnlClass, rr } from "@/lib/format";
import { useAnimated } from "@/effects";
import { cn } from "@/lib/utils";
import type { PlanBrief, PlanNumbers, Range, ReplayTotals, Stats } from "./types";

export type Variant = "a" | "b";
export const readVariant = (): Variant => {
  try { return new URLSearchParams(window.location.search).get("variant") === "b" ? "b" : "a"; } catch { return "a"; }
};

const CELL = "min-w-0 bg-bg-2 px-4 py-3";
const LABEL = "eyebrow truncate";
const VALUE = "mt-1.5 font-mono text-[20px] font-medium leading-none tabular-nums text-text-1";
const CAPTION = "mt-1.5 font-mono text-[11.5px] leading-4 text-text-3";

const n0 = (v: number | null | undefined) => (v === null || v === undefined ? "—" : Math.round(v).toLocaleString());
const range = (r: Range | null, f: (v: number) => string) => (r && r.lo !== null && r.hi !== null ? `${f(r.lo)} – ${f(r.hi)}` : "");

/** A median with its range on a track: a whisker from the 10% to the 90%, the median as a dot (after Leaderboard Table). */
export function RangeBar({ r, max, min = 0, tone = "gold" }: { r: Range | { median: number | null; lo: number | null; hi: number | null }; max: number; min?: number; tone?: "gold" | "ok" | "crit" }) {
  if (r.median === null || r.lo === null || r.hi === null || max <= min) return <div className="h-2" />;
  const f = (v: number) => `${Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100))}%`;
  const c = tone === "ok" ? "bg-ok" : tone === "crit" ? "bg-pnl-down" : "bg-gold";
  return (
    <div className="relative h-2" aria-hidden="true" data-range-bar>
      <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-line-strong/70" />
      <div className={cn("absolute top-1/2 h-1 -translate-y-1/2 rounded-full opacity-45", c)} style={{ left: f(r.lo), width: `calc(${f(r.hi)} - ${f(r.lo)})` }} />
      <div className={cn("absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full shadow-[0_0_0_2px_var(--color-bg-2)]", c)} style={{ left: f(r.median) }} />
    </div>
  );
}

export function FeesMissing({ missing }: { missing: string[] }) {
  if (!missing.length) return null;
  return (
    <span className="inline-flex h-4 shrink-0 items-center gap-1 whitespace-nowrap rounded-full border border-warn/40 bg-warn/10 px-1.5 py-0 align-top font-mono text-[10.5px] leading-[14px] text-warn"
      data-fees-missing data-hint={`Not in the plan's preset yet: ${missing.join(" and ")}. Net cash shows once both are entered; nothing is guessed.`}>
      <AlertTriangle className="size-3" aria-hidden="true" />fees missing</span>
  );
}

interface Metric { key: string; label: string; value: string; caption: string; hint: string; r: Range | null; max: number; tone?: "gold" | "ok" | "crit"; cls?: string }

function metrics(n: PlanNumbers, horizon: number): { hero: Metric; rest: Metric[] } {
  const share = n.payouts_share;
  const cashKnown = n.net_cash !== null;
  const heroR = cashKnown ? n.net_cash : share;
  const heroMax = Math.max(1, heroR?.hi ?? 0);
  const sharePct = n.split_pct !== null && n.split_pct < 1 ? Math.round(n.split_pct * 100) : null;
  const hero: Metric = {
    key: "net", label: cashKnown ? "Net cash" : sharePct ? `Your ${sharePct}% of payouts` : "Payouts to you",
    value: heroR?.median === null || heroR?.median === undefined ? "—" : money0(heroR.median),
    caption: heroR ? `10–90% ${range(heroR, money0)}` : "",
    hint: cashKnown ? `Payouts to you minus every evaluation and activation fee over ${horizon} trading days; median of 1,000 paths, 10–90% range`
      : `Payouts to you over ${horizon} trading days (fees not entered yet, so not net cash); median of 1,000 paths`,
    r: heroR, max: heroMax, tone: "ok", cls: (heroR?.median ?? 0) >= 0 ? "text-pnl-up" : "text-pnl-down",
  };
  const maxEv = Math.max(1, n.evaluations?.hi ?? 1);
  const rest: Metric[] = [
    { key: "evals", label: "Evaluations used", value: n0(n.evaluations?.median), caption: range(n.evaluations, n0), r: n.evaluations, max: maxEv,
      hint: "Evaluations started within the horizon: the first, plus one after every floor" },
    { key: "pass", label: "Pass rate", value: n.pass_rate?.value === null || !n.pass_rate ? "—" : pct(n.pass_rate.value),
      caption: n.pass_rate && n.pass_rate.lo !== null ? `${pct(n.pass_rate.lo)} – ${pct(n.pass_rate.hi ?? 0)}` : "",
      r: n.pass_rate ? { median: n.pass_rate.value, lo: n.pass_rate.lo, hi: n.pass_rate.hi } : null, max: 1, tone: "ok",
      hint: "Finished evaluations that passed (all paths together); the range is per path" },
    { key: "days", label: "Days to pass", value: n.days_to_pass ? n0(n.days_to_pass.median) : "—", caption: range(n.days_to_pass, n0), r: n.days_to_pass,
      max: Math.max(1, n.days_to_pass?.hi ?? 1), hint: "Trading days each passing evaluation took (the minimum trading days included)" },
    { key: "payouts", label: "Payouts taken", value: n0(n.payouts_n?.median), caption: range(n.payouts_n, n0), r: n.payouts_n, max: Math.max(1, n.payouts_n?.hi ?? 1),
      hint: "Payouts requested whenever the plan's rules allow (buffer, minimum and maximum, days, consistency, clock), at the maximum each time" },
    { key: "gross", label: "Payout total", value: n.payouts_gross?.median === null || !n.payouts_gross ? "—" : money0(n.payouts_gross.median ?? 0),
      caption: range(n.payouts_gross, money0), r: n.payouts_gross, max: Math.max(1, n.payouts_gross?.hi ?? 1), tone: "gold",
      hint: "Gross, before the firm's profit split" },
    { key: "fees", label: "Fees paid", value: n.fees ? money0(n.fees.median ?? 0) : "—", caption: n.fees ? range(n.fees, money0) : "not entered", r: n.fees,
      max: Math.max(1, n.fees?.hi ?? 1), tone: "crit", hint: "Evaluation fees (one per evaluation started) and activation fees (one per pass)" },
  ];
  return { hero, rest };
}

/** Variant A: a hero cell (net cash, or payouts to you while fees are missing) beside five compact cells. */
export interface Hero { label: ReactNode; value: ReactNode; cls?: string; chip?: ReactNode; bar?: ReactNode; caption?: ReactNode; side?: ReactNode; hint?: string }

/** A hero cell beside a grid of six cells: Monte Carlo summary A, and the account's own-stage summary (one layout). */
export function HeroGrid({ hero, cells, ...rest }: { hero: Hero; cells: StatCell[] } & Record<`data-${string}`, string | boolean | undefined>) {
  return (
    <div className="grid gap-3 min-[1100px]:grid-cols-[minmax(260px,1.3fr)_3fr]" {...rest}>
      <div className="flex flex-col justify-between rounded-md border border-line-strong/70 bg-bg-2 p-4" data-hint={hero.hint}>
        <div className="flex items-center justify-between gap-2"><span className={LABEL}>{hero.label}</span>{hero.chip}</div>
        <div className={cn("mt-2 font-mono text-[34px] font-medium leading-none tabular-nums tracking-tight", hero.cls)} data-hero>{hero.value}</div>
        <div className="mt-3">{hero.bar ?? <div className="h-2" />}</div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 whitespace-nowrap font-mono text-[11.5px] text-text-3">
          <span>{hero.caption}</span>{hero.side}
        </div>
      </div>
      <div className="grid grid-cols-2 overflow-hidden rounded-md border border-line-strong/70 min-[700px]:grid-cols-3">
        {cells.map((c) => (
          <div key={c.key} className={cn(CELL, "-m-px border-l border-t border-line-strong/50")} data-hint={c.hint} data-metric={c.key}>
            <div className={LABEL}>{c.label}</div>
            <div className={cn(VALUE, c.cls)}>{c.value}</div>
            <div className={CAPTION}>{c.caption || "\u00a0"}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Shares of the paths as one split bar (pass / fail / open, or payout / floor / neither). */
export function SplitBar({ parts }: { parts: { v: number; cls: string }[] }) {
  return (
    <div className="flex h-2 overflow-hidden rounded-full bg-line-strong/50" aria-hidden="true" data-split-bar>
      {parts.map((x, i) => <div key={i} className={x.cls} style={{ width: `${Math.max(0, x.v) * 100}%` }} />)}
    </div>
  );
}

function SummaryA({ n, horizon, luck }: { n: PlanNumbers; horizon: number; luck: PlanNumbers | null }) {
  const { hero, rest } = metrics(n, horizon);
  return (
    <HeroGrid data-plan-numbers data-variant="a"
      hero={{ label: `${hero.label} · median`, value: hero.value, cls: hero.cls, chip: <FeesMissing missing={n.fees_missing} />, hint: hero.hint,
        bar: <RangeBar r={hero.r ?? { median: null, lo: null, hi: null }} max={hero.max} min={Math.min(0, hero.r?.lo ?? 0)} tone="ok" />, caption: hero.caption,
        side: luck?.payouts_share ? <span data-order-luck data-hint="The same backtest days in new orders: how much of the result is the order the days came in">order luck {money0(luck.payouts_share.median ?? 0)}</span> : undefined }}
      cells={rest.map((m) => ({ key: m.key, label: m.label, value: m.value, caption: m.caption, hint: m.hint }))} />
  );
}

/** Variant B: one grid of seven equal cells, every metric with its range bar (net cash lit). */
function SummaryB({ n, horizon }: { n: PlanNumbers; horizon: number }) {
  const { hero, rest } = metrics(n, horizon);
  const all = [hero, ...rest];
  return (
    <div className="grid grid-cols-2 overflow-hidden rounded-md border border-line-strong/70 min-[800px]:grid-cols-4 min-[1300px]:grid-cols-8" data-plan-numbers data-variant="b">
      {all.map((m, i) => (
        <div key={m.key} className={cn(CELL, "-m-px border-l border-t border-line-strong/50", i === 0 && "col-span-2 bg-gold/[.06]")} data-hint={m.hint} data-metric={m.key}>
          <div className="flex items-center justify-between gap-1.5"><span className={LABEL}>{m.label}</span>{i === 0 && <FeesMissing missing={n.fees_missing} />}</div>
          <div className={cn(VALUE, i === 0 && cn(hero.cls, "text-[24px]"))}>{m.value}</div>
          <div className="mt-2.5"><RangeBar r={m.r ?? { median: null, lo: null, hi: null }} max={m.max} min={Math.min(0, m.r?.lo ?? 0)} tone={m.tone} /></div>
          <div className={CAPTION}>{m.caption || " "}</div>
        </div>
      ))}
    </div>
  );
}

export function McSummary({ variant, n, horizon, luck }: { variant: Variant; n: PlanNumbers; horizon: number; luck: PlanNumbers | null }) {
  return variant === "b" ? <SummaryB n={n} horizon={horizon} /> : <SummaryA n={n} horizon={horizon} luck={luck} />;
}

/** The Drawdown replay's totals, in summary A's cell style. */
export function ReplayStrip({ t, end }: { t: ReplayTotals; end: string }) {
  const split = t.split_pct < 1 ? ` · your ${Math.round(t.split_pct * 100)}%: ${money0(t.payouts_share)}` : "";
  const cells: { k: string; label: string; value: ReactNode; caption: string; hint?: string }[] = [
    { k: "evals", label: "Evaluations used", value: <span data-evaluations>{t.evaluations}</span>, caption: `${t.passes} passed · ${t.eval_fails} failed`, hint: "The first evaluation plus one after every floor" },
    { k: "funded", label: "Funded accounts lost", value: t.funded_fails, caption: `ends ${t.ends_in === "funded" ? "funded" : t.ends_in === "evaluation" ? "in an evaluation" : "closed"}`, hint: `The backtest's last day: ${end}` },
    { k: "payouts", label: "Payouts", value: t.payouts_n, caption: `${money0(t.payouts_gross)} gross${split}`, hint: "At the maximum each time the plan's rules allow" },
    { k: "net", label: t.net_cash !== null ? "Net cash" : "Payouts to you", value: <span className={pnlClass(t.net_cash ?? t.payouts_share)}>{money0(t.net_cash ?? t.payouts_share)}</span>,
      caption: t.fees !== null ? `fees ${money0(t.fees)}` : "", hint: t.net_cash !== null ? "Payouts to you minus every fee" : "Fees aren't in the plan's preset yet: net cash shows once they are" },
  ];
  return <StatGrid cols={4} data-replay-summary cells={cells.map((c) => ({ key: c.k, label: c.label, value: c.value, hint: c.hint,
    caption: c.k === "net" && t.fees_missing.length ? <FeesMissing missing={t.fees_missing} /> : c.caption }))} />;
}

/** Compare plans: one row per plan, the pass rate as a bar with its range, then the money columns (after Leaderboard Table). */
export function ComparePlans({ rows, selected, onPick, horizon }: { rows: (PlanBrief & { numbers: PlanNumbers | null })[]; selected: string; onPick: (id: string) => void; horizon: number }) {
  const maxCash = Math.max(1, ...rows.map((r) => (r.numbers?.net_cash ?? r.numbers?.payouts_share)?.hi ?? 0));
  return (
    <div className="overflow-x-auto rounded-md border border-line-strong/70 bg-bg-2" data-compare>
      <div className="min-w-[760px]">
        <div className="grid grid-cols-[minmax(180px,1.3fr)_minmax(150px,1fr)_72px_104px_80px_minmax(170px,1.2fr)] items-center gap-3 whitespace-nowrap border-b border-line-strong/50 px-4 py-2 text-[10.5px] font-semibold uppercase tracking-[0.1em] text-text-3">
          <div>Plan</div><div>Pass rate</div><div className="text-right">Evals</div><div className="text-right">Days to pass</div><div className="text-right">Payouts</div>
          <div className="text-right" data-hint={`Over ${horizon} trading days; net cash once fees are entered`}>Payouts to you / net</div>
        </div>
        <div className="group/rows">
          {rows.map((r) => {
            const n = r.numbers, cash = n?.net_cash ?? n?.payouts_share ?? null, sel = r.id === selected;
            return (
              <button key={r.id} type="button" onClick={() => onPick(r.id)} aria-pressed={sel} data-compare-row={r.id}
                className={cn("grid w-full grid-cols-[minmax(180px,1.3fr)_minmax(150px,1fr)_72px_104px_80px_minmax(170px,1.2fr)] items-center gap-3 border-b border-line-strong/30 px-4 py-2.5 text-left transition-[opacity,background-color] duration-150 last:border-b-0",
                  "group-hover/rows:opacity-50 hover:!opacity-100 hover:bg-white/[.03]", sel && "bg-gold/[.07]")}>
                <div className="min-w-0"><div className="truncate text-[13px] font-medium text-text-1">{r.label}</div>
                  <div className="truncate font-mono text-[11px] text-text-3">{[r.evaluation && `${money0(r.evaluation.start_usd)} start`, r.evaluation && `${money0(r.evaluation.drawdown_usd)} drawdown`, r.evaluation && `${money0(r.evaluation.risk_usd)} a trade`].filter(Boolean).join(" · ")}</div></div>
                <div className="flex items-center gap-2.5">
                  <div className="flex-1"><RangeBar r={n?.pass_rate ? { median: n.pass_rate.value, lo: n.pass_rate.lo, hi: n.pass_rate.hi } : { median: null, lo: null, hi: null }} max={1} tone="ok" /></div>
                  <span className="w-12 text-right font-mono text-[13px] text-text-1">{n?.pass_rate?.value != null ? pct(n.pass_rate.value) : "—"}</span>
                </div>
                <div className="text-right font-mono text-[13px] text-text-2">{n0(n?.evaluations?.median)}</div>
                <div className="text-right font-mono text-[13px] text-text-2">{n0(n?.days_to_pass?.median)}</div>
                <div className="text-right font-mono text-[13px] text-text-2">{n0(n?.payouts_n?.median)}</div>
                <div className="flex items-center gap-2.5">
                  <div className="flex-1"><RangeBar r={cash ?? { median: null, lo: null, hi: null }} max={maxCash} tone="gold" /></div>
                  <span className={cn("w-[72px] text-right font-mono text-[13px]", pnlClass(cash?.median ?? 0))}>{cash?.median != null ? money0(cash.median) : "—"}</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── the stats box under the KPI tiles ──
/** A number that glides to its new value (the number ticker, DeltaNumberTicker 44850; Calm: at once). */
export function Num({ v, f, animated }: { v: number | null; f: (n: number) => string; animated: boolean }) {
  if (v === null) return <>—</>;
  return <DeltaNumberTicker value={v} format={f} showAbsolute={false} showPercent={false} ghost={false} tooltip={false}
    duration={animated ? 420 : 0} valueClassName="!mt-0 !leading-[inherit]" tabIndex={-1}
    // inline in the cell's text: no gap for the (unused) delta chip, no ghost line below
    className="!inline-flex !gap-0 align-baseline [&>div>span+span]:hidden [&>span]:hidden" />;
}

function statItems(s: Stats, unitR: boolean, animated: boolean) {
  const N = (x: number | null, f: (n: number) => string) => <Num v={x} f={f} animated={animated} />;
  const m = (n: number) => money(n), int = (n: number) => String(Math.round(n)), one = (n: number) => n.toFixed(1);
  return {
    net: unitR ? <span className={pnlClass(s.net_r)}>{N(s.net_r, rr)}</span> : <span className={pnlClass(s.net_usd)}>{N(s.net_usd, m)}</span>,
    wl: <>{N(s.wins, int)} / {N(s.losses, int)}</>,
    avgWin: N(s.avg_win_usd, m), avgLoss: N(s.avg_loss_usd, m),
    bigWin: N(s.largest_win_usd, m), bigLoss: N(s.largest_loss_usd, m),
    ddClose: N(s.max_dd_close_usd, m), ddIntra: N(s.max_dd_intraday_usd, m),
    streakLong: <>{N(s.longest_win_streak, int)} / {N(s.longest_loss_streak, int)}</>,
    streakAvg: <>{N(s.avg_win_streak, one)} / {N(s.avg_loss_streak, one)}</>,
  };
}

/** Stats box A: one bordered grid, ten cells (after Stats Grid). */
function StatsA({ s, unitR, animated }: { s: Stats; unitR: boolean; animated: boolean }) {
  const i = statItems(s, unitR, animated);
  const cells: [string, ReactNode, string?][] = [
    ["Net", i.net], ["Wins / losses", i.wl], ["Average win", i.avgWin, "text-pnl-up"], ["Average loss", i.avgLoss, "text-pnl-down"],
    ["Longest streak W / L", i.streakLong], ["Largest win", i.bigWin, "text-pnl-up"], ["Largest loss", i.bigLoss, "text-pnl-down"],
    ["Max DD · close to close", i.ddClose], ["Max DD · intraday", i.ddIntra], ["Average streak W / L", i.streakAvg],
  ];
  return (
    <dl className="m-0 mt-3 grid grid-cols-2 overflow-hidden rounded-md border border-line-strong/70 min-[800px]:grid-cols-5" data-stats-panel data-variant="a">
      {cells.map(([k, val, cls]) => (
        <Spotlight key={k} enabled={animated} className="-m-px min-w-0 border-l border-t border-line-strong/50 bg-bg-2/90 px-4 py-2.5" data-stat-cell>
          <dt className={LABEL}>{k}</dt><dd className={cn("m-0 mt-1 font-mono text-[15px] tabular-nums", cls ?? "text-text-1")}>{val}</dd>
        </Spotlight>
      ))}
    </dl>
  );
}

/** Stats box B: four grouped cards, each a pair of related numbers; wins vs losses as a split bar (after Insight Cards). */
function StatsB({ s, unitR, animated }: { s: Stats; unitR: boolean; animated: boolean }) {
  const i = statItems(s, unitR, animated);
  const wr = s.wins + s.losses ? s.wins / (s.wins + s.losses) : 0;
  const pair = (a: [string, ReactNode, string?], b: [string, ReactNode, string?]) => (
    <div className="mt-2 grid grid-cols-2 gap-3">
      {[a, b].map(([k, v, cls]) => (
        <div key={k} className="min-w-0"><dt className="truncate text-[11.5px] text-text-3">{k}</dt><dd className={cn("m-0 mt-0.5 font-mono text-[15px] tabular-nums", cls ?? "text-text-1")}>{v}</dd></div>
      ))}
    </div>
  );
  const card = (title: string, body: ReactNode, head?: ReactNode) => (
    <Spotlight enabled={animated} className="min-w-0 rounded-md border border-line-strong/70 bg-bg-2/90 px-4 py-3" data-stat-cell>
      <div className="flex items-baseline justify-between gap-2"><span className={LABEL}>{title}</span>{head}</div>{body}
    </Spotlight>
  );
  return (
    <dl className="m-0 mt-3 grid grid-cols-2 gap-3 min-[800px]:grid-cols-3 min-[1200px]:grid-cols-5" data-stats-panel data-variant="b">
      {card("Result", <>
        {pair(["Net", i.net], ["Wins / losses", i.wl])}
        <div className="mt-2 flex h-1 overflow-hidden rounded-full bg-pnl-down/50" aria-hidden="true"><div className="bg-pnl-up" style={{ width: `${wr * 100}%` }} /></div>
      </>)}
      {card("Averages", pair(["Average win", i.avgWin, "text-pnl-up"], ["Average loss", i.avgLoss, "text-pnl-down"]))}
      {card("Largest trades", pair(["Largest win", i.bigWin, "text-pnl-up"], ["Largest loss", i.bigLoss, "text-pnl-down"]))}
      {card("Max drawdown", pair(["Close to close", i.ddClose], ["Intraday", i.ddIntra]))}
      {card("Streaks W / L", pair(["Longest", i.streakLong], ["Average", i.streakAvg]))}
    </dl>
  );
}

export function StatsBox({ variant, s, unitR, animated }: { variant: Variant; s: Stats; unitR: boolean; animated: boolean }) {
  return variant === "b" ? <StatsB s={s} unitR={unitR} animated={animated} /> : <StatsA s={s} unitR={unitR} animated={animated} />;
}

/** Headline numbers as their own small stats (owner 2026-11-15: label above, value below, like the KPI tiles), one
 * bordered grid (after Stats Grid 29195); detail in the caption and the hint. */
export interface StatCell { key: string; label: string; value: ReactNode; caption?: ReactNode; hint?: string; cls?: string; bar?: { v: number; color: string } }
export function StatGrid({ cells, cols = 5, ...rest }: { cells: StatCell[]; cols?: 4 | 5 | 6 } & Record<`data-${string}`, string | boolean | undefined>) {
  const animated = useAnimated();
  const wide = cols === 6 ? "min-[1300px]:grid-cols-6" : cols === 5 ? "min-[1100px]:grid-cols-5" : "min-[1100px]:grid-cols-4";
  return (
    <div className={cn("grid grid-cols-2 overflow-hidden rounded-md border border-line-strong/70 min-[800px]:grid-cols-3", wide)} {...rest}>
      {cells.map((c) => (
        <Spotlight key={c.key} enabled={animated} className={cn(CELL, "-m-px border-l border-t border-line-strong/50 bg-bg-2/90 pb-[18px]")} data-hint={c.hint} data-metric={c.key} data-stat-cell="">
          <div className={LABEL}>{c.label}</div>
          <div className={cn(VALUE, c.cls)}>{c.value}</div>
          <div className={cn(CAPTION, "truncate")} data-hint={typeof c.caption === "string" ? c.caption : undefined}>{c.caption ?? " "}</div>
          {c.bar && (
            <div className="absolute inset-x-4 bottom-[8px] h-[3px] overflow-hidden rounded-full bg-line-strong/40" data-cell-bar aria-hidden="true">
              <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.max(0, Math.min(1, c.bar.v)) * 100}%`, background: c.bar.color }} />
            </div>
          )}
        </Spotlight>
      ))}
    </div>
  );
}
