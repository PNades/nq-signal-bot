// One account card (design-system/pages/accounts.md): name, firm and preset, a state badge with its icon, the drawdown
// rule, lives as pips, risk per trade and the daily profit limit; today's est. P&L and the drawdown left come with the
// live engine (step 22) and show "—" until then. Tilt (21st.dev Tilt Card 12244, picked) in Full, never in Calm or on
// an account in danger (from step 22: near its limit, unknown, maybe filled), which shows a crit outline and its words.
import { CircleAlert, CircleCheck, Clock, FlaskConical, PencilLine, TriangleAlert } from "lucide-react";
import { TiltCard } from "@/components/ui/tilt-card";
import { LivesPips } from "@/components/ui/form-controls";
import { useAnimated } from "@/effects";
import { money0 } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Preset, Row } from "./types";

export function ruleText(a: Record<string, unknown>) {
  const dd = money0(Number(a.drawdown_usd));
  if (a.drawdown_type === "static") return `${dd} static`;
  const lock = a.drawdown_lock_usd != null ? money0(Number(a.drawdown_lock_usd)) : null;
  if (!lock) return `${dd} EOD-trailing, no lock`;
  const when = a.drawdown_lock_trigger === "first_payout" ? "at the first payout" : a.drawdown_lock_trigger === "floor_or_first_payout" ? "at the floor or first payout" : "when the floor gets there";
  return `${dd} EOD-trailing, locks at ${lock} ${when}`;
}

function Badge({ tone, icon: Icon, children }: { tone: "cream" | "ok" | "warn" | "crit" | "stale"; icon?: typeof Clock; children: React.ReactNode }) {
  const t = { cream: "border-gold/40 text-gold", ok: "border-ok/40 text-ok", warn: "border-warn/50 text-warn", crit: "border-crit/50 text-crit", stale: "border-line-strong text-text-2" }[tone];
  return <span className={cn("inline-flex h-5 items-center gap-1 rounded-xs border px-1.5 font-mono text-[10px] tracking-[0.06em]", t)}>{Icon && <Icon className="size-3" aria-hidden="true" />}{children}</span>;
}

export function AccountCard({ row, presets, onOpen }: { row: Row; presets: Preset[]; onOpen: () => void }) {
  const animated = useAnimated();
  const a = row.account;
  const d = row.derived;
  const preset = presets.find((p) => p.id === a.preset);
  const errors = row.issues.filter((i) => i.level === "error").length;
  const warnings = row.issues.filter((i) => i.level === "warning").length;
  const danger = false; // live states (near a limit, unknown, maybe filled) arrive with the engine at step 22
  const pending = a.pending_risk as { max_risk_fraction_divisor: number } | null | undefined;
  return (
    <TiltCard tiltLimit={6} scale={1.02} effect="gravitate" disabled={!animated || danger}
      className={cn("rounded-md border bg-bg-2", danger ? "border-crit" : "border-line-strong/70 hover:border-line-strong")}>
      <button type="button" onClick={onOpen} className="flex w-full flex-col gap-4 p-5 text-left" data-account-card={String(a.name)}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="font-mono text-[18px] leading-6 text-text-1">{String(a.name)}</div>
            <div className="mt-0.5 truncate text-[13px] text-text-2" data-hint={`${String(a.firm)}${preset ? ` · ${preset.label} · ${preset.stage}` : ""}`}>{preset ? `${preset.label} · ${preset.stage}` : String(a.firm)}</div>
          </div>
          <div className="flex shrink-0 flex-wrap justify-end gap-1">
            {a.draft ? <Badge tone="warn" icon={PencilLine}>DRAFT</Badge> : null}
            <Badge tone="cream">{a.phase === "funded" ? "FUNDED" : "EVALUATION"}</Badge>
            {a.demo ? <Badge tone="stale" icon={FlaskConical}>DEMO</Badge> : null}
            {a.active === false ? <Badge tone="stale">INACTIVE</Badge> : null}
            {a.status && a.status !== "active" ? <Badge tone={a.status === "passed" ? "ok" : a.status === "failed" ? "crit" : "stale"}>{String(a.status).toUpperCase()}</Badge> : null}
          </div>
        </div>
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="eyebrow">Drawdown</div>
            <div className="mt-1 truncate text-[13px] text-text-1">{ruleText(a)}</div>
          </div>
          {d?.lives != null && (
            <div className="flex shrink-0 flex-col items-end gap-1">
              <div className="eyebrow">{d.lives} lives</div>
              <LivesPips lives={d.lives} />
            </div>
          )}
        </div>
        <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-line-strong/50 pt-4">
          <div><dt className="eyebrow">Risk per trade</dt><dd className="m-0 mt-1 font-mono text-[15px] text-text-1">{d?.risk_usd_per_trade != null ? money0(d.risk_usd_per_trade) : "—"} <span className="text-[12px] text-text-3">{d?.fraction}</span></dd></div>
          <div><dt className="eyebrow">Daily profit limit</dt><dd className="m-0 mt-1 font-mono text-[15px] text-text-1">{d?.daily_profit_limit_usd != null ? money0(d.daily_profit_limit_usd) : "none"}</dd></div>
          <div><dt className="eyebrow">Today est. P&amp;L</dt><dd className="m-0 mt-1 flex items-center gap-1 font-mono text-[15px] text-text-3"><Clock className="size-3.5" aria-hidden="true" />—</dd></div>
          <div><dt className="eyebrow">Drawdown left</dt><dd className="m-0 mt-1 flex items-center gap-1 font-mono text-[15px] text-text-3"><Clock className="size-3.5" aria-hidden="true" />—</dd></div>
        </dl>
        <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
          {errors > 0 ? <span className="flex items-center gap-1 text-crit"><CircleAlert className="size-3.5" aria-hidden="true" />{errors} to fix</span>
            : <span className="flex items-center gap-1 text-text-2"><CircleCheck className="size-3.5 text-ok" aria-hidden="true" />rules pass their checks</span>}
          {warnings > 0 && <span className="flex items-center gap-1 text-warn"><TriangleAlert className="size-3.5" aria-hidden="true" />{warnings} warning{warnings === 1 ? "" : "s"}</span>}
          {pending && <span className="flex items-center gap-1 text-warn"><Clock className="size-3.5" aria-hidden="true" />1/{pending.max_risk_fraction_divisor} waits for the sweep</span>}
          <span className="ml-auto text-text-3">{a.draft ? "Check and save →" : "Edit →"}</span>
        </div>
      </button>
    </TiltCard>
  );
}
