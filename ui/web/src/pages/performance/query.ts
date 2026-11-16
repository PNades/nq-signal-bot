// The Performance page's data address, shared by the page and its preload (owner 2026-11-16: a page change shows the
// next page only once its data is loaded, so both must ask for exactly the same answer).
import { load } from "@/lib/prefetch";
import type { PerfPage } from "./types";

export const REAL_PHASES = new Set(["demo", "prop", "live"]);

/** the query string of /api/performance: backtest phases ask for every account (the plan prices them), real-trades
 * phases for the selected account */
export function perfQuery({ account, phase, planId, start = null, end = null }: {
  account: string; phase: string; planId: string | null; start?: string | null; end?: string | null;
}): string {
  const real = REAL_PHASES.has(phase);
  const q = new URLSearchParams({ account: real ? account : "all", phase });
  if (!real && planId) q.set("plan", planId);
  if (start) q.set("start", start);
  if (end) q.set("end", end);
  return q.toString();
}

export const perfUrl = (query: string) => `/api/performance?${query}`;

/** the page's phase, kept while the panel is open, so leaving and coming back (e.g. the breakdown's Edit, then back)
 * shows the same phase */
export const perfMemory = { phase: "development" };

/** The answers the page needs to open filled: the selection's data (for a backtest phase, then with its first plan,
 * which the page picks at once). */
export async function preloadPerformance(focus: string | null): Promise<void> {
  const phase = perfMemory.phase, account = focus ?? "all";
  const first = await load<PerfPage>(perfUrl(perfQuery({ account, phase, planId: null })));
  if (!REAL_PHASES.has(phase) && first.plans.length) await load(perfUrl(perfQuery({ account, phase, planId: first.plans[0].id })));
}
