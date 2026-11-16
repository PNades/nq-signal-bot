import type { FieldIssue } from "@/components/ui/form-controls";
import type { Acc } from "./fields";

export interface Derived {
  risk_usd_per_trade: number | null; risk_cap_usd: number | null; fraction: string | null; lives: number | null;
  daily_profit_limit_usd: number | null; daily_profit_limit_note: string; size_cap_minis: number | null;
  min_stop_full_risk_pts: Record<string, number | null>; max_stop_one_contract_pts: Record<string, number | null>; one_contract: string;
}
export interface Report { derived: Derived | null; issues: FieldIssue[]; ok: boolean }
export interface Row extends Report { account: Acc }
export interface Preset { id: string; label: string; firm: string; stage: string; fields: Acc; builtin: boolean }
export interface AccountsData { hash: string; presets_hash: string; accounts: Row[]; presets: Preset[]; folder: string }
export interface RiskEffect {
  new_divisor: number; new_risk_usd: number; lives_new: number; lives_old: number; losses_that_fail: number; rr: number;
  rows: { stop_pts: number; effective_risk_usd: number; limited_by: string }[]; confirm_text: string;
}
