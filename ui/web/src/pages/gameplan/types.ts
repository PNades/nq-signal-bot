// The Gameplan page's data (GET /api/gameplan, src/nqbot/ui/api_gameplan.py; the gameplan's shape is SPEC §5.6).
export type Range = [number, number] | null;
export interface Flag { field: string; origin: string; reason: string; review?: boolean }
export interface Target { as_written?: string; low: number; high: number }
export interface EsGate { as_written?: string; direction?: string; price?: Range }
export interface Invalidation { as_written?: string; direction?: string; price?: number | null; timeframe?: string; instrument?: string }
export interface Entry {
  id: string; side: "buy" | "sell"; zone: Range; stop: number | null; targets: Target[];
  trigger?: { type?: string; note?: string; nq_price?: Range; es_gates?: EsGate[] } | null;
  es_gates?: EsGate[]; invalidation?: Invalidation[]; other_conditions?: string[]; source_quote?: string;
}
export interface Scenario { id: string; bias: string; prob: number | null; desc: string }
export interface ScenarioSet { frame: string; label_as_written?: string; scenarios: Scenario[] }
export interface Gameplan {
  date: string; status: string; flags: Flag[]; zones: Entry[]; hvl: Range; hvl0: Range; regime_line: Range;
  scenario_sets: ScenarioSet[]; other_conditions: string[];
  source: { file: string; kind: string; cost_usd?: number; model_served?: string; parser_version?: number };
  parse?: { date_source?: string; version_used?: string | null; dropped?: string[] };
}
export interface Release { at: string; name: string; live_only: boolean; from: string; to: string }
export interface Rules {
  trading_day: boolean; early_close: string | null; blackouts: Release[]; blackout_before_min: number; blackout_after_min: number;
  windows: { rth_open: string; golden_zone_from: string; all_setups_from: string; last_entry: string; flat_by: string };
}
export interface Status {
  state: "none" | "parsed" | "confirmed" | "changed";
  review_flags?: number; info_flags?: number; edits?: number; parse_status?: string;
  confirmed_at?: string; confirmed_date?: string; confirmed_note?: string; counts_for_trading?: boolean;
  accepted?: number; before_open?: boolean;
}
export interface Edit { path: unknown[]; value: unknown; reason: string; at: string }
export interface GameplanData {
  date: string; today: string; synthetic: boolean; rules: Rules; gameplan: Gameplan | null; status: Status;
  edits: Edit[]; days: string[]; cap_usd: number; accept: string[]; empty?: string;
  day_states?: Record<string, { state: Status["state"]; counts_for_trading?: boolean; review_flags?: number }>;
  parsed?: { file: string; status: string; cost_usd: number; estimate_usd: number | null };
}
export interface Uploaded {
  upload?: string; name: string; date?: string; cached?: boolean; input_tokens?: number | null; worst_usd?: number | null;
  cap_usd?: number; over_cap?: boolean; replaces?: boolean; after_open?: boolean; already?: string;
}
