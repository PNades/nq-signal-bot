// The Review API's shapes (src/nqbot/review/cards.py, store.py; ui/api_review.py). Times are UTC seconds; the chart
// draws them shifted by the card's `et_offset_sec`, so its UTC axis reads New York wall time.
export type Bar = [number, number, number, number, number, number]; // start, open, high, low, close, volume
export type Seg = [number, number, number, number];                 // from, to, lo, hi
export type RoleRow = [number, "ceiling" | "floor" | null, boolean, boolean, number]; // t, role, provisional, crossed back, flips

export interface Level {
  uid: string; level_id: string; label: string; sub_type: string | null; group: string; node: boolean;
  chain: string | null; strike: number | null; segs: Seg[]; roles: RoleRow[]; marks: [number, string][];
}
export type ZonePt = [number, number | null, number | null, number, "ceiling" | "floor" | null, boolean]; // t, lo, hi, members, role, provisional
export interface Zone { id: string; own: boolean; pts: ZonePt[]; end: number | null }
export interface Touch { t: number; price: number | null; dir: number; role: string; own: boolean; label: string | null }
export interface MapSnap { t: number; spot: number; r: number; king: number | null; max: number; s: [number, number][] }
export type MapName = "QQQ" | "NDXP" | "SPY" | "SPX";
/** today's developing daily VAH / VAL (owner 2026-11-24, display only): [from, to, price], each minute as of its close
 * (the decision's own minute as of the decision); from the round's sidecar, absent on cards without one */
export type DevSeg = [number, number, number];
export interface DevVa { vah: DevSeg[]; val: DevSeg[] }
export interface Half {
  bars: Record<"NQ" | "ES", Bar[]>;
  dev_va?: DevVa;
  levels: Record<"NQ" | "ES", Level[]>;
  zones: Zone[];
  events: Touch[];
  maps: Record<MapName, MapSnap[]>;
  kings: Record<MapName, [number, number, number][]>;
}
export interface Reading { group: string; label: string; value: string; note?: string }
export interface Trade {
  style: string; order_type: string; direction: "long" | "short"; entry: number | null; stop: number; target: number;
  stop_pts: number; target_pts: number; net_rr: number; stop_rule: string | null; target_kind: string | null;
  target_zone_kind: string | null; size: { minis: number; micros: number } | null;
}
export interface Outcome {
  result: "target" | "stop" | "timeout" | string; r: number | null; t_exit: number | null; exit: number | null;
  t_fill: number | null; fill: number | null; mae: number | null; mfe: number | null; structure: string | null;
  structure_how: string | null; timeout_reason: string | null;
}
export interface Answer {
  verdict: "take" | "skip"; reason: string; entry_choice: string | null; stop: number | null; target: number | null;
  confidence: number | null; correct: boolean | null; result?: string | null; r?: number | null; answered_at?: string;
}
export interface Card {
  id: string; day: string; decision_id: string; pair_id: string; event_id: string; stratum?: string | null; why?: string | null;
  symbols: Record<string, string | null>; et_offset_sec: number;
  /** `reveal_end` is null until the answer (it would tell when the trade ended) */
  t: { decision: number; decision_et: string; chart_start: number; replay_start: number; minute: number; reveal_end: number | null; open: number; close: number };
  setup: {
    event_type: string | null; role: string | null; direction: number; golden_zone_part: string | null;
    zone: { id: string; lo: number; hi: number; center: number; near_edge: number };
    members: string[]; founding: string[]; stack_count: number; price: number; touch_price: number;
    primary_member: string | null; audit_or_sweep: boolean; trigger: string | null;
  };
  trade: Trade;
  readings: Reading[];
  /** the confluences behind "Levels stacked", as known at the decision time */
  stack?: { count: number; other: number; items: { label: string; price?: number; lo?: number; hi?: number; role?: "ceiling" | "floor" | null; provisional?: boolean; crossed_back?: boolean; flips?: number; outside_pts?: number | null; missing?: boolean; fit?: "inside" | "overlapping" | "outside" }[]; rule?: string };
  pre: Half;
  post?: Half;
  outcome?: Outcome;
  answer: Answer | null;
}
export interface RoundCard { id: string; day: string; answered: boolean; verdict: string | null; correct: boolean | null; stratum?: string | null }
export interface RoundSummary {
  id: string; build_key: string | null; labels_key: string | null; created: string | null; months: number[] | null;
  n: number; answered: number; correct: number; cards: RoundCard[]; synthetic: boolean; preview?: boolean;
}
export interface ReviewIndex {
  synthetic: boolean; rounds: { id: string; n: number; build_key?: string; created?: string }[];
  round: RoundSummary | null; empty: string | null;
}
