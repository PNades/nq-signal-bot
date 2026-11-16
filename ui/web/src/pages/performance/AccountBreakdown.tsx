// The "All accounts" breakdown (owner 2026-11-16): one row per account with trades in this real-trades phase, archived
// ones included: firm, plan, stage, status, first / last day, net P&L, payouts to you, fees paid, net cash; a totals row
// across every account ever. Sortable by any column; a row click focuses that account.
// From 21st.dev (searched "sortable data table dark with totals footer row status badges"): the frame, status badges
// and totals footer after "Card Frame Table" (cnippet-dev v-table-3, id 22188) and "Invoice History Table" (v-table-12,
// id 22187); the sorting and row rhythm of our Trades table // sorting and row rhythm of our Trades table (Data Table 28327 pattern). Not copied: our own markup.
// Owner 2026-11-16: each row ends with a small Edit action that opens the account in the editor (archived ones too: the
// only way to past accounts, the Accounts page lists active ones); the row-actions column after 21st.dev "Table Row
// Actions" 23696 (one action here, so a ghost button instead of its menu; not copied).
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Pencil } from "lucide-react";
import { money, money0, pnlClass } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Breakdown, BreakdownRow } from "./types";

type Key = "account" | "firm" | "plan" | "stage" | "status" | "start" | "end" | "trades" | "net_usd" | "payouts" | "fees" | "net_cash";
const COLS: { key: Key; label: string; num?: boolean; hint?: string }[] = [
  { key: "account", label: "Account" }, { key: "firm", label: "Firm" }, { key: "plan", label: "Plan" }, { key: "stage", label: "Stage" },
  { key: "status", label: "Status" }, { key: "start", label: "Start" }, { key: "end", label: "End" }, { key: "trades", label: "Trades", num: true },
  { key: "net_usd", label: "Net P&L", num: true }, { key: "payouts", label: "Payouts", num: true, hint: "Paid to you: the payouts after the firm's profit split" },
  { key: "fees", label: "Fees", num: true, hint: "The evaluation's price (one-time), or a funded account's activation fee" },
  { key: "net_cash", label: "Net cash", num: true, hint: "Payouts to you minus fees" },
];
const TONE: Record<string, string> = { active: "border-ok/40 text-ok", passed: "border-gold/40 text-gold", failed: "border-crit/50 text-crit", closed: "border-line-strong text-text-2" };
const DAY = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
// the Edit column stays in view when the table scrolls sideways (960 px)
const STICK = "sticky right-0 z-[1] shadow-[-8px_0_12px_-8px_rgba(0,0,0,.6)]";
const day = (d: string | null) => (d ? DAY.format(new Date(d + "T12:00:00Z")) : "—");

export function AccountBreakdown({ data, focus, onPick, onEdit, editable }: {
  data: Breakdown; focus: string | null; onPick: (name: string) => void;
  /** open the account in the editor; `editable` says which accounts are in the accounts file (not the synthetic stand-ins) */
  onEdit: (name: string) => void; editable: (name: string) => boolean;
}) {
  const [sort, setSort] = useState<{ key: Key; asc: boolean }>({ key: "start", asc: true });
  const rows = useMemo(() => {
    const v = (r: BreakdownRow) => r[sort.key] ?? (COLS.find((c) => c.key === sort.key)?.num ? Number.NEGATIVE_INFINITY : "");
    return [...data.rows].sort((a, b) => { const x = v(a), y = v(b); return (x < y ? -1 : x > y ? 1 : 0) * (sort.asc ? 1 : -1); });
  }, [data.rows, sort]);
  const t = data.totals;
  const th = "border-b border-line-strong px-3 py-2 font-medium text-text-2 whitespace-nowrap";
  return (
    <div className="mt-3 overflow-hidden rounded-md border border-line-strong/70 bg-bg-2" data-breakdown>
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line-strong/50 px-4 py-2.5">
        <span className="eyebrow">Every account · {t.accounts} account{t.accounts === 1 ? "" : "s"}{t.archived ? ` · ${t.archived} archived` : ""}</span>
        <span className="font-mono text-[11px] text-text-3">click a row to focus that account · Edit opens it in the editor</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12.5px]">
          <thead>
            <tr>
              {COLS.map((c) => {
                const on = sort.key === c.key;
                return (
                  <th key={c.key} aria-sort={on ? (sort.asc ? "ascending" : "descending") : "none"} className={cn(th, c.num ? "text-right" : "text-left")}>
                    <button type="button" data-hint={c.hint} onClick={() => setSort((s) => ({ key: c.key, asc: s.key === c.key ? !s.asc : !c.num }))}
                      className={cn("inline-flex items-center gap-1 hover:text-text-1", on && "text-text-1")}>
                      {c.label}{on && (sort.asc ? <ArrowUp className="size-3" aria-hidden="true" /> : <ArrowDown className="size-3" aria-hidden="true" />)}
                    </button>
                  </th>
                );
              })}
              <th className={cn(th, STICK, "w-px bg-bg-2 text-right")}><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {rows.map((r) => (
              <tr key={r.account} onClick={() => onPick(r.account)} data-breakdown-row={r.account} aria-selected={focus === r.account}
                className={cn("group cursor-pointer border-b border-line-strong/30 transition-colors hover:bg-white/[.035]", r.archived && "text-text-2", focus === r.account && "bg-gold/[.07]")}>
                <td className="whitespace-nowrap px-3 py-2 text-text-1">{r.account}</td>
                <td className="whitespace-nowrap px-3 py-2 font-sans text-text-2">{r.firm ?? "—"}</td>
                <td className="whitespace-nowrap px-3 py-2 font-sans">{r.plan ?? "—"}</td>
                <td className="whitespace-nowrap px-3 py-2 font-sans text-text-2">{r.stage ?? "—"}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  <span className={cn("inline-flex h-5 items-center rounded-xs border px-1.5 text-[10px] uppercase", TONE[r.status] ?? TONE.closed)}>{r.status}</span>
                  {r.archived && <span className="ml-1.5 text-[10px] uppercase text-text-3">archived</span>}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-text-2">{day(r.start)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-text-2">{r.end ? day(r.end) : <span className="text-ok">ongoing</span>}</td>
                <td className="px-3 py-2 text-right">{r.trades}</td>
                <td className={cn("whitespace-nowrap px-3 py-2 text-right", pnlClass(r.net_usd))}>{money(r.net_usd)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">{r.payouts ? money0(r.payouts) : "—"}{r.payouts_n ? <span className="text-text-3"> · {r.payouts_n}</span> : null}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right text-text-2">{r.fees === null ? "—" : money(r.fees, false)}</td>
                <td className={cn("whitespace-nowrap px-3 py-2 text-right", r.net_cash === null ? "" : pnlClass(r.net_cash))}>{r.net_cash === null ? "—" : money(r.net_cash)}</td>
                <td className={cn(STICK, "whitespace-nowrap bg-bg-2 px-2 py-1 text-right group-hover:[background-image:linear-gradient(rgba(255,255,255,.035),rgba(255,255,255,.035))]")}>
                  {editable(r.account) ? (
                    <button type="button" onClick={(e) => { e.stopPropagation(); onEdit(r.account); }} aria-label={`Edit ${r.account}`} data-breakdown-edit={r.account}
                      className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-transparent px-2 font-sans text-[12px] text-text-2 transition-colors hover:border-line-strong hover:bg-white/[.04] hover:text-text-1 focus-visible:border-line-strong focus-visible:text-text-1">
                      <Pencil className="size-3.5" aria-hidden="true" />Edit</button>
                  ) : (
                    <span className="inline-flex h-7 items-center px-2 font-sans text-[11.5px] text-text-3" data-hint="Synthetic account: not in your accounts file, so there is nothing to edit" data-breakdown-noedit={r.account}>synthetic</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot className="font-mono" data-breakdown-totals>
            <tr className="bg-bg-1/80">
              <td className="px-3 py-2.5 font-sans text-[12px] font-medium uppercase tracking-[0.05em] text-text-2" colSpan={7}>Totals · every account ever</td>
              <td className="px-3 py-2.5 text-right text-text-1">{t.trades}</td>
              <td className={cn("whitespace-nowrap px-3 py-2.5 text-right", pnlClass(t.net_usd))}>{money(t.net_usd)}</td>
              <td className="whitespace-nowrap px-3 py-2.5 text-right text-text-1">{money0(t.payouts)}</td>
              <td className="whitespace-nowrap px-3 py-2.5 text-right text-text-1" data-hint={t.fees_unknown ? `${t.fees_unknown} account(s) without a known fee` : undefined}>{money(t.fees, false)}{t.fees_unknown ? "*" : ""}</td>
              <td className={cn("whitespace-nowrap px-3 py-2.5 text-right font-medium", pnlClass(t.net_cash))}>{money(t.net_cash)}</td>
              <td className={cn(STICK, "bg-bg-1")} />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}
