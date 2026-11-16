// The Accounts page's form, field by field, in the groups of SPEC §9b item 1 (the §6.7 field names). The server
// (src/nqbot/accounts/) is the one source of truth for checks and derived values; this file only says how each field is
// shown and edited. `when` hides a field that doesn't apply (e.g. payout fields on an evaluation).
export type Acc = Record<string, unknown>;
export type Kind = "text" | "money" | "number" | "int" | "pct" | "bool" | "time" | "date" | "seg" | "lastlife" | "divisor" | "tiers" | "after";

export interface FieldDef {
  key: string;
  label: string;
  kind: Kind;
  unit?: string;
  help?: string;
  options?: { value: string; label: string }[];
  when?: (a: Acc) => boolean;
  mono?: boolean;
  wide?: boolean;
}

export interface Group { id: string; title: string; titleItalic: string; note?: string; fields: FieldDef[] }

const funded = (a: Acc) => a.phase === "funded";
const evaluation = (a: Acc) => a.phase === "evaluation";

export const GROUPS: Group[] = [
  {
    id: "account", title: "The", titleItalic: "account", fields: [
      { key: "name", label: "Name", kind: "text", mono: true, help: "lowercase letters and digits, e.g. mffur1" },
      { key: "firm", label: "Firm", kind: "text" },
      { key: "phase", label: "Phase", kind: "seg", options: [{ value: "evaluation", label: "Evaluation" }, { value: "funded", label: "Funded" }] },
      { key: "active", label: "Active", kind: "bool", help: "only active accounts trade and get lives and caps" },
      { key: "status", label: "Status", kind: "seg", options: [{ value: "active", label: "Active" }, { value: "passed", label: "Passed" }, { value: "failed", label: "Failed" }, { value: "closed", label: "Closed" }],
        help: "passed, failed or closed archives the account: hidden from the active list, always in All accounts and its totals" },
      { key: "started", label: "Started", kind: "date", help: "empty = its first trade's day" },
      { key: "ended", label: "Ended", kind: "date", when: (a) => !!a.status && a.status !== "active", help: "empty = its last trade's day" },
      { key: "demo", label: "Demo account", kind: "bool", help: "Tradovate demo (steps 21–25); outside the direction lock" },
      { key: "traderspost_webhook_env", label: "Webhook variable", kind: "text", mono: true, help: "the .env variable NAME, e.g. TP_WEBHOOK_MFFUR1; never the URL" },
      { key: "sim_fills", label: "Sim fills", kind: "bool", help: "Tradovate sim: a resting limit fills on a touch" },
    ],
  },
  {
    id: "targets", title: "Targets", titleItalic: "& caps", fields: [
      { key: "profit_cap_mode", label: "Daily profit limit", kind: "seg", options: [{ value: "consistency", label: "Consistency rule" }, { value: "none", label: "None" }] },
      { key: "consistency_pct", label: "Consistency", kind: "pct", unit: "%", when: (a) => a.profit_cap_mode === "consistency", help: "the cap = this share of the target (plus any dip below the base)" },
      { key: "profit_target_usd", label: "Profit target", kind: "money", unit: "$" },
      { key: "stop_at_target", label: "Stop at target", kind: "bool", when: evaluation, help: "no new entries once the evaluation is passed" },
      { key: "funded_buffer_usd", label: "Funded buffer", kind: "money", unit: "$", when: funded },
      { key: "payout_min_usd", label: "Payout min", kind: "money", unit: "$", when: funded },
      { key: "payout_max_usd", label: "Payout max", kind: "money", unit: "$", when: funded, help: "blank = no cap" },
      { key: "payout_target_usd", label: "Payout target", kind: "money", unit: "$", when: funded, help: "blank = the payout minimum" },
      { key: "payout_profit_since_last_usd", label: "Profit needed since the last payout", kind: "money", unit: "$", when: funded },
      { key: "payout_pct_of_profit", label: "Payout share of profit", kind: "pct", unit: "%", when: funded, help: "capped by the payout max (Lucid Flex: 50%)" },
      { key: "payout_profit_days_min", label: "Profitable days per payout cycle", kind: "int", unit: "days", when: funded },
      { key: "payout_profit_day_min_usd", label: "…each with at least", kind: "money", unit: "$", when: funded },
      { key: "payout_wait_calendar_days", label: "Payout wait", kind: "int", unit: "days", when: funded, help: "calendar days before each payout, from the first funded trade, then from the previous payout" },
      { key: "first_funded_trade_date", label: "First funded trade", kind: "date", when: funded, help: "the payout clock starts here (editable)" },
      { key: "last_payout_date", label: "Last payout date", kind: "date", when: funded, help: "the current payout cycle starts here (until the engine records payouts, step 22)" },
      { key: "payout_clock_hold", label: "While the payout clock runs", kind: "seg", when: funded, options: [{ value: "off", label: "Off" }, { value: "pause", label: "Pause" }, { value: "protect", label: "Protect" }] },
      { key: "profit_split_pct", label: "Profit split", kind: "pct", unit: "%", when: funded, help: "reports only" },
      { key: "payout_cap_group", label: "Payout cap group", kind: "text", mono: true, when: funded },
      { key: "payout_cap_group_usd", label: "Group's cumulative cap", kind: "money", unit: "$", when: funded },
      { key: "copied_accounts", label: "Copied accounts", kind: "int", help: "accounts copying this one through TradeSyncer (0 = none)" },
      { key: "auto_protect_within_usd", label: "Auto-protect within", kind: "money", unit: "$", when: funded, help: "switch to Protect this close to the payout target" },
      { key: "payouts_taken", label: "Payouts taken", kind: "int", when: funded },
      { key: "after_first_payout", label: "After the first payout", kind: "after", when: funded, wide: true },
      { key: "firm_balance_from_zero", label: "Firm shows the balance from $0", kind: "bool", when: funded, help: "Lucid funded: typed balances get the start balance added" },
      { key: "flip_hold_max_sec", label: "Flipping: trades held ≤", kind: "number", unit: "s", help: "report only (Lucid: 5 s)" },
      { key: "flip_profit_share_max", label: "…firm's share limit", kind: "pct", unit: "%" },
      { key: "flip_profit_share_warn", label: "…warn above", kind: "pct", unit: "%" },
    ],
  },
  {
    id: "drawdown", title: "Drawdown", titleItalic: "& losses", fields: [
      { key: "start_balance_usd", label: "Start balance", kind: "money", unit: "$" },
      { key: "drawdown_usd", label: "Max drawdown", kind: "money", unit: "$" },
      { key: "drawdown_type", label: "Drawdown type", kind: "seg", options: [{ value: "eod_trailing", label: "EOD trailing" }, { value: "static", label: "Static" }] },
      { key: "drawdown_lock_usd", label: "Floor locks at", kind: "money", unit: "$", when: (a) => a.drawdown_type === "eod_trailing", help: "blank = never locks (every MFFU evaluation)" },
      { key: "drawdown_lock_trigger", label: "Locks when", kind: "seg", when: (a) => a.drawdown_type === "eod_trailing", wide: true,
        options: [{ value: "floor", label: "The floor gets there" }, { value: "first_payout", label: "First payout" }, { value: "floor_or_first_payout", label: "Whichever first" }] },
      { key: "dll_enabled", label: "Daily loss limit", kind: "bool", help: "a breach ends the day, not the account" },
      { key: "firm_daily_loss_limit_usd", label: "Firm's daily loss limit", kind: "money", unit: "$", when: (a) => a.dll_enabled === true },
      { key: "bot_daily_loss_cap_usd", label: "Bot daily loss cap", kind: "money", unit: "$", help: "blank = the firm's DLL (none when it is off)" },
      { key: "max_losses_per_day", label: "Max losses per day", kind: "int", help: "optional" },
    ],
  },
  {
    id: "firm", title: "Firm", titleItalic: "rules", fields: [
      { key: "min_trading_days", label: "Minimum trading days", kind: "int", unit: "days" },
      { key: "max_inactive_days", label: "Max inactive days", kind: "int", unit: "days", help: "calendar days without a trade before the firm closes it" },
      { key: "payout_min_days", label: "Payout minimum days", kind: "int", unit: "days", when: funded },
      { key: "commission_per_mini_rt", label: "Commission per NQ", kind: "money", unit: "$ RT" },
      { key: "commission_per_micro_rt", label: "Commission per MNQ", kind: "money", unit: "$ RT" },
    ],
  },
  {
    id: "limits", title: "Position", titleItalic: "limits", fields: [
      { key: "max_trades_per_day", label: "Max trades per day", kind: "int", help: "optional" },
      { key: "max_minis", label: "Max minis", kind: "int" },
      { key: "max_micros", label: "Max micros", kind: "int" },
      { key: "micro_mini_ratio", label: "Micros per mini", kind: "int", when: (a) => a.max_contracts_total == null },
      { key: "max_contracts_total", label: "Total contracts (counted alike)", kind: "int", help: "blank = the micros-per-mini rule (Pro 50k funded: 5)" },
      { key: "allow_micros", label: "Micros allowed", kind: "bool" },
      { key: "allow_mixed", label: "Mixing allowed", kind: "bool", help: "an NQ leg and an MNQ leg in one trade" },
      { key: "scaling_tier_update", label: "Scaling tier updates", kind: "seg", options: [{ value: "intraday", label: "Intraday" }, { value: "eod", label: "End of day" }] },
      { key: "scaling_tiers", label: "Scaling plan", kind: "tiers", wide: true },
    ],
  },
  {
    id: "sizing", title: "Sizing", titleItalic: "& trade rules", fields: [
      { key: "max_risk_fraction_divisor", label: "Risk fraction of the drawdown", kind: "divisor", wide: true },
      { key: "risk_usd_per_trade", label: "Risk per trade", kind: "money", unit: "$", help: "follows the fraction: floor(drawdown ÷ N); may be typed lower" },
      { key: "flat_by", label: "Flat by", kind: "time", unit: "ET" },
      { key: "breakeven_enabled", label: "Breakeven", kind: "bool" },
      { key: "lives_mode", label: "Lives mode", kind: "bool" },
      { key: "last_life_top_pct", label: "Last-life cutoff", kind: "lastlife" },
      { key: "max_loss_run_rate", label: "Max loss-run rate", kind: "pct", unit: "%" },
    ],
  },
];

export const FIELD_GROUP: Record<string, string> = Object.fromEntries(GROUPS.flatMap((g) => g.fields.map((f) => [f.key, g.id])));
