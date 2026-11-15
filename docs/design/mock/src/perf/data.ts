// MOCK DATA for the step-14a Performance mock (not results): a seeded, repeatable set of backtest-style trades over
// development months 1-6 (2025-10 to 2026-03), New York trading dates, net of commissions, on two accounts that carry
// their presets' real rules (SPEC §6.7). Every number shown on the mock page comes from here and is labelled MOCK DATA.

export interface Trade {
  id: string; date: string; time: string; account: string; dir: "Long" | "Short"; style: "c" | "d";
  size: string; entry: number; exit: number; r: number; usd: number;
}
export interface Day { date: string; trades: Trade[]; usd: number; r: number; wins: number; losses: number }

// ── Accounts and their drawdown rules (SPEC §6.7; the same fields as config/accounts.yaml) ────────────────────────
export type DrawdownType = "static" | "eod_trailing";
export type LockTrigger = "floor" | "first_payout" | "floor_or_first_payout";
export interface DrawdownRule {
  startUsd: number; drawdownUsd: number; type: DrawdownType;
  /** eod_trailing: the floor value at which it stops rising; null = never locks */
  lockUsd: number | null; lockTrigger: LockTrigger;
}
export interface Account {
  id: string; preset: string; phase: "evaluation" | "funded"; riskUsd: number;
  commissionMiniRt: number; commissionMicroRt: number; rule: DrawdownRule;
}
export const ACCOUNTS: Account[] = [
  { id: "mffur1", preset: "MFFU Rapid EOD 25k", phase: "evaluation", riskUsd: 333, commissionMiniRt: 4.68, commissionMicroRt: 1.9,
    rule: { startUsd: 25000, drawdownUsd: 1000, type: "eod_trailing", lockUsd: null, lockTrigger: "floor" } },
  { id: "lucid1", preset: "Lucid Flex 25k", phase: "funded", riskUsd: 333, commissionMiniRt: 3.5, commissionMicroRt: 1.0,
    rule: { startUsd: 25000, drawdownUsd: 1000, type: "eod_trailing", lockUsd: 25100, lockTrigger: "floor_or_first_payout" } },
];
export type AccountSel = "all" | string;
export const accountById = (id: string) => ACCOUNTS.find((a) => a.id === id)!;

const usd0 = (v: number) => `$${v.toLocaleString("en-US")}`;
/** The rule in words, for panel titles. */
export function ruleText(rule: DrawdownRule) {
  const dd = usd0(rule.drawdownUsd);
  if (rule.type === "static") return `${dd} static: floor ${usd0(rule.startUsd - rule.drawdownUsd)}`;
  if (rule.lockUsd === null) return `${dd} EOD-trailing, no lock`;
  const lock = usd0(rule.lockUsd);
  if (rule.lockTrigger === "first_payout") return `${dd} EOD-trailing until the first payout, then locked at ${lock}`;
  if (rule.lockTrigger === "floor_or_first_payout") return `${dd} EOD-trailing, locked at ${lock} once it gets there or at the first payout`;
  return `${dd} EOD-trailing, locked at ${lock} once it gets there`;
}

/**
 * The drawdown floor after each session end (SPEC §6.7 Account state, the prop simulator's rule):
 * static: start − drawdown; eod_trailing: high water (EOD balances, start included) − drawdown, then by the lock
 * trigger: `floor` = min(trailing, lock); `first_payout` = trailing until the first payout, the lock from then on;
 * `floor_or_first_payout` = min(trailing, lock) until the first payout, the lock from then on.
 * A breach = an end-of-day balance at or below the floor in force (the previous session end's); the mock has EOD
 * balances only (the step-15 simulator also checks intraday equity). `payoutAt` = index of the session end of the
 * first payout (the mock models no payouts: null).
 */
export function floorPath(rule: DrawdownRule, eod: ArrayLike<number>, payoutAt: number | null = null) {
  const n = eod.length;
  const floor = new Float64Array(n), high = new Float64Array(n);
  let hw = rule.startUsd, lockedAt: number | null = null, breachAt: number | null = null;
  let prevFloor = rule.startUsd - rule.drawdownUsd;
  for (let i = 0; i < n; i++) {
    if (breachAt === null && eod[i] <= prevFloor) breachAt = i;
    hw = Math.max(hw, eod[i]);
    let f: number;
    if (rule.type === "static") f = rule.startUsd - rule.drawdownUsd;
    else {
      const trail = hw - rule.drawdownUsd;
      const paid = payoutAt !== null && i >= payoutAt;
      if (rule.lockUsd === null) f = trail;
      else if (rule.lockTrigger === "first_payout") f = paid ? rule.lockUsd : trail;
      else if (rule.lockTrigger === "floor_or_first_payout") f = paid ? rule.lockUsd : Math.min(trail, rule.lockUsd);
      else f = Math.min(trail, rule.lockUsd);
      if (lockedAt === null && rule.lockUsd !== null && f === rule.lockUsd) lockedAt = i;
    }
    floor[i] = f; high[i] = hw; prevFloor = f;
  }
  return { floor, high, lockedAt, breachAt };
}

// ── The seeded trades ─────────────────────────────────────────────────────────────────────────────────────────────
function rng(seed: number) {
  let s = seed >>> 0;
  return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
}

const HOLIDAYS = new Set(["2025-11-27", "2025-12-25", "2026-01-01", "2026-01-19", "2026-02-16"]);

function tradingDays(): string[] {
  const out: string[] = [];
  for (let d = new Date(Date.UTC(2025, 9, 1)); d <= new Date(Date.UTC(2026, 2, 31)); d.setUTCDate(d.getUTCDate() + 1)) {
    const wd = d.getUTCDay();
    const key = d.toISOString().slice(0, 10);
    if (wd !== 0 && wd !== 6 && !HOLIDAYS.has(key)) out.push(key);
  }
  return out;
}

export const DATES = tradingDays();
const SEED = 267; // picked so neither account breaches in the backtest order and the Lucid floor locks (its Monte Carlo still shows the risk)

function build() {
  const r = rng(SEED);
  const trades: Trade[] = [];
  let n = 0;
  let px = 21000;
  for (const date of DATES) {
    // a gentle improving trend over the six months, so the mock KPIs show a mix of better and worse
    const month = (Number(date.slice(0, 4)) - 2025) * 12 + Number(date.slice(5, 7)) - 10;
    const count = r() < 0.28 ? 0 : r() < 0.55 ? 1 : r() < 0.85 ? 2 : 3;
    for (let i = 0; i < count; i++) {
      const win = r() < 0.40 + 0.03 * month;
      const rr = win ? +(1.2 + r() * 2.4).toFixed(2) : r() < 0.15 ? 0 : -1;
      const dir = r() < 0.5 ? "Long" : "Short";
      px += (r() - 0.5) * 120;
      const stopPts = 10 + Math.round(r() * 16);
      const entry = Math.round(px * 4) / 4;
      const exit = Math.round((entry + (dir === "Long" ? 1 : -1) * rr * stopPts) * 4) / 4;
      const minutes = 9 * 60 + 45 + Math.floor(r() * 300);
      const acc = ACCOUNTS[r() < 0.55 ? 0 : 1];
      const mixed = r() >= 0.7;
      const commission = mixed ? acc.commissionMiniRt + 3 * acc.commissionMicroRt : 2 * acc.commissionMiniRt;
      trades.push({
        id: `T${++n}`, date, time: `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`,
        account: acc.id, dir, style: r() < 0.55 ? "d" : "c", size: mixed ? "1 NQ + 3 MNQ" : "2 NQ",
        entry, exit, r: rr, usd: +(rr * acc.riskUsd - commission).toFixed(2),
      });
    }
  }
  return trades;
}

export const ALL_TRADES = build();

function daysOf(trades: Trade[]): Day[] {
  const by = new Map<string, Trade[]>();
  for (const t of trades) { const l = by.get(t.date); if (l) l.push(t); else by.set(t.date, [t]); }
  return DATES.map((date) => {
    const ts = by.get(date) ?? [];
    return { date, trades: ts, usd: +ts.reduce((a, t) => a + t.usd, 0).toFixed(2), r: +ts.reduce((a, t) => a + t.r, 0).toFixed(2),
      wins: ts.filter((t) => t.usd > 0).length, losses: ts.filter((t) => t.usd < 0).length };
  });
}

/** monthly series for the KPI sparklines: one point per month */
export const MONTHS = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"];
export const MONTH_LABELS = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar"];

export interface View {
  days: Day[]; trades: Trade[];
  curve: { date: string; usd: number; r: number; dd: number }[];
  stats: { net: number; netR: number; rPerTrade: number; trades: number; winRate: number; profitFactor: number; maxDD: number };
  monthly: (f: (days: Day[]) => number) => number[];
}

const views = new Map<AccountSel, View>();
/** Everything the page shows for one account or all of them (cached). */
export function viewOf(sel: AccountSel): View {
  const hit = views.get(sel);
  if (hit) return hit;
  const trades = sel === "all" ? ALL_TRADES : ALL_TRADES.filter((t) => t.account === sel);
  const days = daysOf(trades);
  let usd = 0, r = 0, peak = 0;
  const curve = days.map((d) => { usd += d.usd; r += d.r; peak = Math.max(peak, usd); return { date: d.date, usd: +usd.toFixed(2), r: +r.toFixed(2), dd: +(usd - peak).toFixed(2) }; });
  const wins = trades.filter((t) => t.usd > 0), losses = trades.filter((t) => t.usd < 0);
  const gross = wins.reduce((a, t) => a + t.usd, 0), lossSum = -losses.reduce((a, t) => a + t.usd, 0);
  const netR = curve.at(-1)!.r;
  const v: View = {
    days, trades, curve,
    stats: { net: curve.at(-1)!.usd, netR, rPerTrade: trades.length ? netR / trades.length : 0, trades: trades.length,
      winRate: wins.length / Math.max(1, wins.length + losses.length), profitFactor: gross / Math.max(1, lossSum), maxDD: Math.min(...curve.map((c) => c.dd)) },
    monthly: (f) => MONTHS.map((m) => f(days.filter((d) => d.date.startsWith(m)))),
  };
  views.set(sel, v);
  return v;
}

/** One account's end-of-day balances with its floor and high water under its own rule. */
export interface AccountRun {
  account: Account; dates: string[]; balance: number[]; floor: number[]; high: number[];
  lockedAt: number | null; breachAt: number | null;
}
const runs = new Map<string, AccountRun>();
export function accountRun(id: string): AccountRun {
  const hit = runs.get(id);
  if (hit) return hit;
  const account = accountById(id), v = viewOf(id);
  const balance = v.curve.map((c) => account.rule.startUsd + c.usd);
  const fp = floorPath(account.rule, balance);
  const run: AccountRun = { account, dates: v.curve.map((c) => c.date), balance, floor: Array.from(fp.floor), high: Array.from(fp.high), lockedAt: fp.lockedAt, breachAt: fp.breachAt };
  runs.set(id, run);
  return run;
}

/**
 * Monte Carlo per account: 1,000 reshuffles of the account's whole days (never single trades), each path run through
 * the account's own drawdown rule; P(breach) = share of paths whose end-of-day balance reaches the floor in force.
 * Bands are profit above the start balance per day index.
 */
export interface McResult {
  account: Account; n: number; pBreach: number; medianBreachDay: number | null;
  bands: { p5: number; p25: number; p50: number; p75: number; p95: number }[];
  worst: number[]; best: number[]; base: number[]; baseFloor: number[];
}
const mcs = new Map<string, McResult>();
export function monteCarlo(id: string): McResult {
  const hit = mcs.get(id);
  if (hit) return hit;
  const account = accountById(id), rule = account.rule, run = accountRun(id);
  const pnl = viewOf(id).days.map((d) => d.usd), n = pnl.length, N = 1000;
  const r = rng(7 + id.length * 101);
  const paths: Float64Array[] = [];
  let hits = 0;
  const breachDays: number[] = [];
  for (let k = 0; k < N; k++) {
    const bal = new Float64Array(n);
    let b = rule.startUsd;
    for (let i = 0; i < n; i++) { b += pnl[Math.floor(r() * n)]; bal[i] = b; }
    const fp = floorPath(rule, bal);
    if (fp.breachAt !== null) { hits++; breachDays.push(fp.breachAt + 1); }
    const p = new Float64Array(n + 1);
    for (let i = 0; i < n; i++) p[i + 1] = bal[i] - rule.startUsd;
    paths.push(p);
  }
  const q = (arr: number[], f: number) => { const s = [...arr].sort((a, b) => a - b); return s[Math.floor(f * (s.length - 1))]; };
  const bands = Array.from({ length: n + 1 }, (_, i) => {
    const col = paths.map((p) => p[i]);
    return { p5: q(col, 0.05), p25: q(col, 0.25), p50: q(col, 0.5), p75: q(col, 0.75), p95: q(col, 0.95) };
  });
  const dd = (p: Float64Array) => { let peak = 0, m = 0; for (const v of p) { peak = Math.max(peak, v); m = Math.min(m, v - peak); } return m; };
  const sorted = [...paths].sort((a, b) => dd(a) - dd(b));
  const res: McResult = {
    account, n: N, pBreach: hits / N, medianBreachDay: breachDays.length ? q(breachDays, 0.5) : null, bands,
    worst: Array.from(sorted[0]), best: Array.from(sorted.at(-1)!),
    base: [0, ...run.balance.map((v) => v - rule.startUsd)],
    baseFloor: [rule.startUsd - rule.drawdownUsd, ...run.floor].map((v) => v - rule.startUsd),
  };
  mcs.set(id, res);
  return res;
}
