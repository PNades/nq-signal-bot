// The Performance API's shapes (src/nqbot/ui/api_performance.py, src/nqbot/performance/view.py).
export interface Stats {
  trades: number; wins: number; losses: number; net_usd: number; net_r: number; r_per_trade: number | null;
  win_rate: number | null; avg_win_usd: number | null; avg_loss_usd: number | null; largest_win_usd: number | null;
  largest_loss_usd: number | null; profit_factor: number | null; max_dd_close_usd: number; max_dd_intraday_usd: number | null;
  longest_win_streak: number; longest_loss_streak: number; avg_win_streak: number | null; avg_loss_streak: number | null;
}
export interface Notice { phase: string; label: string; count: number; reason: string; gate: string | null }
export interface EquityPoint { time: string | number; date: string; clock?: string; usd: number; r: number; dd_usd: number; day_usd: number; day_r: number; trades: number; phase: string; start?: boolean; trade?: string }
export interface PhaseStart { time: string | number; date: string; phase: string; label: string }
export interface Run {
  account: string; rule: string; rule_type: "static" | "eod_trailing"; start_usd: number; drawdown_usd: number;
  dates: string[]; balance: number[]; floor: number[]; high: number[]; room: number[];
  locked_at: number | null; breach_at: number | null; closest_at: number | null;
}
export interface Monthly { month: string; net_usd: number; net_r: number; trades: number; r_per_trade: number | null; win_rate: number | null; profit_factor: number | null; max_dd_usd: number }
export interface AccountBrief { name: string; kind: string | null; phase: string | null; demo: boolean; draft: boolean; rule: string | null; status?: string; archived?: boolean; editable?: boolean }
// "All accounts" in a real-trades phase (owner 2026-11-16; src/nqbot/performance/view.py breakdown)
export interface BreakdownRow {
  account: string; firm: string | null; plan: string | null; stage: string | null; status: string; archived: boolean; demo: boolean;
  start: string; end: string | null; last_trade: string; trades: number; net_usd: number; payouts_n: number; payouts_gross: number; payouts: number;
  fees: number | null; net_cash: number | null;
}
export interface Breakdown { rows: BreakdownRow[]; totals: { accounts: number; archived: number; trades: number; net_usd: number; payouts: number; fees: number; fees_unknown: number; net_cash: number } }
export interface Band { p5: number; p50: number; p95: number }
export interface PerfPage {
  selection: { account: string; phase: string; start: string | null; end: string | null };
  kpis: Stats; per_phase: Record<string, Stats & { label: string }>; phases_present: string[]; notices: Notice[];
  equity: { points: EquityPoint[]; phase_starts: PhaseStart[]; per_trade: boolean }; monthly: Monthly[]; drawdown: Run[];
  live_vs_expected: { live_r: number[]; band: Band[]; trades: number };
  accounts: AccountBrief[]; sources: Record<string, string>; synthetic: boolean; gates_passed: string[]; months: Record<string, string>;
  plan: PlanBrief | null; plans: PlanBrief[]; kpi_trend: KpiTrend; breakdown?: Breakdown;
}
export interface McFan {
  n: number; days: number; method: string; start_usd: number; floor0: number; target_balance: number | null;
  p_breach: number | null; median_breach_day: number | null; p_target: number | null; median_target_day: number | null;
  median_end: number | null; order_luck: { p_breach: number | null; median_breach_day: number | null; p_target: number | null } | null;
  bands: { p5: number; p25: number; p50: number; p75: number; p95: number }[]; worst: number[]; best: number[];
  base: number[]; base_floor: number[];
}
export interface AcctType {
  id: string; label: string; stage: string; kind: string; rule: string; risk_usd: number; daily_profit_cap: number | null;
  daily_loss_cap: number | null; start_usd: number; drawdown_usd: number; target_balance: number | null;
}
export interface TypeRow extends AcctType {
  p_breach: number | null; median_breach_day: number | null; p_target: number | null; median_target_day: number | null;
  median_end: number | null; order_luck: McFan["order_luck"];
}
export interface AcctState { balance_usd: number; high_water_usd: number; floor_usd: number; as_of: string | null; source: "engine" | "ledger" | "synthetic"; trades?: number }
export interface McTypes { mode: "types"; types: TypeRow[]; selected: string; fan: McFan; dates: string[]; source: string; synthetic: boolean }

export interface DdTypes { mode: "types"; types: AcctType[]; selected: string; run: Run; source: string; synthetic: boolean }

export interface CalCell { date: string; in_month: boolean; value: number | null; trades: number; wins: number; losses: number; sign: number | null }
export interface CalMonth {
  month: string; unit: "usd" | "r"; months: string[]; notice: { phase: string; count: number; reason: string } | null;
  weeks: { days: CalCell[]; week: { value: number | null; days_traded: number; trades: number; wins: number; losses: number; win_rate: number | null } }[];
  header: { value: number | null; trading_days: number; green_days: number; red_days: number; trades: number; wins: number; losses: number;
    win_rate: number | null; best: { date: string; value: number } | null; worst: { date: string; value: number } | null };
}
export interface DayTrade { id: string; time: string; account: string; direction: string; style: string; size: string; entry: number; exit: number; net_usd: number; r: number; source: string }
export interface TradeRow {
  id: string; date: string; entry_time: string; exit_time: string; account: string; source: string; direction: string; style: string;
  size: string; entry: number; exit: number; net_usd: number; r: number; commission_usd: number; phase: string;
}
export type Unit = "usd" | "r";
// plans and their lifecycle with payouts (strategy view; owner 2026-11-15; src/nqbot/performance/lifecycle.py)
export interface PayoutBrief {
  baseline_usd: number; need_usd: number; threshold_usd: number; max_usd: number | null; pct_of_profit: number | null;
  profit_since_last_usd: number | null; min_days: number | null; wait_calendar_days: number | null; profit_days_min: number | null;
  profit_day_min_usd: number | null; consistency_pct: number | null; split_pct: number | null; cap_group_usd: number | null;
}
export interface PlanBrief {
  id: string; label: string; kind: string; builtin: boolean; evaluation: AcctType | null; funded: AcctType | null; after_first: AcctType | null;
  min_trading_days: number; payout: PayoutBrief | null; fees: { evaluation_usd: number | null; activation_usd: number | null }; fees_missing: string[];
}
/** a median across the paths with its 10–90% range */
export interface Range { median: number | null; lo: number | null; hi: number | null }
export interface PlanNumbers {
  evaluations: Range | null; pass_rate: { value: number | null; lo: number | null; hi: number | null } | null; days_to_pass: Range | null;
  paths_passing: number | null; payouts_n: Range | null; payouts_gross: Range | null; payouts_share: Range | null; paths_with_payout: number | null;
  fees: Range | null; net_cash: Range | null; fees_missing: string[]; split_pct: number | null; ends_funded: number | null;
}
export type LifeKind = "passed" | "payout" | "failed" | "capped";
export interface LifeEvent { day: number; kind: LifeKind; amount?: number; n?: number; days?: number; stage?: string; balance?: number; date?: string }
export type StageName = "evaluation" | "funded" | "ended";
export interface Trace { balance: number[]; floor: (number | null)[]; target: (number | null)[]; payout_line: (number | null)[]; stage: StageName[]; events: LifeEvent[] }
export interface PlanFan {
  n: number; horizon: number; days: number; start_usd: number; numbers: PlanNumbers | null; order_luck: PlanNumbers | null;
  bands: McFan["bands"]; base: Trace;
}
export interface McPlans { mode: "plans"; plans: PlanBrief[]; selected: string; horizon: number; horizons: number[]; fan: PlanFan;
  compare: (PlanBrief & { numbers: PlanNumbers | null })[] | null; source: string; synthetic: boolean }
export interface ReplayTotals {
  evaluations: number; passes: number; eval_fails: number; funded_fails: number; payouts_n: number; payouts_gross: number;
  payouts_share: number; split_pct: number; fees: number | null; net_cash: number | null; fees_missing: string[]; ends_in: StageName;
}
export interface Replay extends Omit<Trace, "events"> { dates: string[]; start_usd: number; events: (LifeEvent & { date: string })[]; totals: ReplayTotals | null }
export interface DdPlans { mode: "plans"; plans: PlanBrief[]; selected: string; horizon: number; horizons: number[]; replay: Replay; source: string; synthetic: boolean }
// one actual account "from today", its own stage only (owner 2026-11-15, rework 4; src/nqbot/performance/account_mc.py)
export interface AcctNumbers {
  stage: "evaluation" | "funded"; p_fail: number; days_to_fail: Range | null;
  p_pass?: number; days_to_pass?: Range | null; p_open?: number;
  p_payout?: number; days_to_payout?: Range | null; payouts_n?: Range; payouts_gross?: Range; payouts_share?: Range;
}
export interface AcctFan {
  n: number; horizon: number; days: number; stage: "evaluation" | "funded"; start: { balance: number; high: number; floor: number; payouts: number; days_in: number; cycle_from?: string };
  numbers: AcctNumbers | null; order_luck: AcctNumbers | null; bands: McFan["bands"]; split_pct: number | null; base: Trace; rules: AcctType; payout: PayoutBrief | null;
  min_trading_days: number;
}
export interface McAccount { mode: "account"; account: AccountBrief; horizon: number; horizons: number[]; state: AcctState | null; reason?: string; fan?: AcctFan; source: string; synthetic: boolean }
export interface AcctRun extends Run { target: (number | null)[]; payout_line: (number | null)[]; stage: StageName[]; times: number[]; clocks: string[]; pnl: (number | null)[]; day_first: number[]; per_trade: boolean; floor_now: number; high_now: number;
  closest_room: number | null; lock_usd: number | null; lock_trigger: "floor" | "first_payout" | "floor_or_first_payout"; start_floor: number; payouts_taken: number }
export interface DdAccount { mode: "account"; account: AccountBrief; horizon: number; horizons: number[]; run: AcctRun | null; reason?: string; stage?: "evaluation" | "funded"; payout?: PayoutBrief | null; synthetic: boolean }
export interface KpiTrend {
  dates: string[]; series: Record<"net_usd" | "net_r" | "trades" | "usd_per_trade" | "r_per_trade" | "win_rate" | "profit_factor" | "dd_usd", (number | null)[]>;
  compare: { days: number; recent: KpiCmp; earlier: KpiCmp } | null;
}
export interface KpiCmp { net_per_day_usd: number; net_per_day_r: number; trades_per_day: number; usd_per_trade: number | null; r_per_trade: number | null; win_rate: number | null; profit_factor: number | null; max_dd_usd: number }
