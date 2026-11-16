// Trades table (design-system/pages/performance.md), from the step-14a mock, on the API's trade rows (the ledger guard
// applied on the server): sortable, filterable, sticky header, mono numbers right-aligned, a column chooser; rows are
// windowed past 200 (the page's performance budget). Pattern from 21st.dev shadcn Data Table (1050) and the aria-sort
// header pattern of 33506.
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3, Search } from "lucide-react";
import { money, pnlClass, rr } from "@/lib/format";
import { load, peek } from "@/lib/prefetch";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/shell/page";
import type { TradeRow } from "./types";

type Key = "date" | "entry_time" | "account" | "direction" | "style" | "size" | "entry" | "exit" | "net_usd" | "r" | "commission_usd" | "phase";
const COLS: { key: Key; label: string; num?: boolean; off?: boolean }[] = [
  { key: "date", label: "Date" }, { key: "entry_time", label: "Time" }, { key: "account", label: "Account" }, { key: "phase", label: "Phase" },
  { key: "direction", label: "Dir" }, { key: "style", label: "Style" }, { key: "size", label: "Size" },
  { key: "entry", label: "Entry", num: true }, { key: "exit", label: "Exit", num: true },
  { key: "commission_usd", label: "Commission", num: true, off: true }, { key: "net_usd", label: "Net $", num: true }, { key: "r", label: "Net R", num: true },
];
const px = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const ROW_H = 31, WINDOW_FROM = 200, OVERSCAN = 20;

/** The table's data address (the Performance page loads it before an account switch shows, owner 2026-11-16). */
export const tradesUrl = (query: string) => `/api/performance/trades?${query}`;
// the sort, the search and the columns carry over when the table is drawn again for another account (this browser tab)
const prefs: { sort: { key: Key; asc: boolean }; q: string; shown: Set<Key> } = { sort: { key: "date", asc: false }, q: "", shown: new Set(COLS.filter((c) => !c.off).map((c) => c.key)) };

export function TradesTable({ query, label }: { query: string; label: string }) {
  const url = tradesUrl(query);
  const [got, setGot] = useState<TradeRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sort, setSort] = useState(prefs.sort);
  const [q, setQ] = useState(prefs.q);
  const [shown, setShown] = useState<Set<Key>>(prefs.shown);
  useEffect(() => { prefs.sort = sort; prefs.q = q; prefs.shown = shown; }, [sort, q, shown]);
  const [pick, setPick] = useState(false);
  const [scroll, setScroll] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const ctl = new AbortController();
    setErr(null);
    load<{ trades: TradeRow[] }>(url, ctl.signal).then((d) => setGot(d.trades)).catch((e) => { if (!ctl.signal.aborted) setErr(String(e?.message ?? e)); });
    return () => ctl.abort();
  }, [url]);
  // an answer already loaded shows in the same render (no placeholder); otherwise the old rows stay until it arrives
  const rows = peek<{ trades: TradeRow[] }>(url)?.trades ?? got;
  const list = useMemo(() => {
    const f = q.trim().toLowerCase();
    const l = (rows ?? []).filter((t) => !f || `${t.date} ${t.account} ${t.direction} ${t.style} ${t.phase}`.toLowerCase().includes(f));
    return [...l].sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      const c = typeof x === "number" ? (x as number) - (y as number) : String(x).localeCompare(String(y)) || a.entry_time.localeCompare(b.entry_time);
      return sort.asc ? c : -c;
    });
  }, [rows, sort, q]);
  if (err) return <p className="text-[13px] text-text-2">{err}</p>;
  if (!rows) return <Skeleton className="h-[440px]" />;
  const cols = COLS.filter((c) => shown.has(c.key));
  // windowing past WINDOW_FROM rows: only the rows in view (plus OVERSCAN) are rendered, spacers keep the height
  const windowed = list.length > WINDOW_FROM;
  const viewH = box.current?.clientHeight ?? 440;
  const first = windowed ? Math.max(0, Math.floor(scroll / ROW_H) - OVERSCAN) : 0;
  const last = windowed ? Math.min(list.length, Math.ceil((scroll + viewH) / ROW_H) + OVERSCAN) : list.length;
  const cell = (t: TradeRow, k: Key) => {
    switch (k) {
      case "entry": case "exit": return px.format(t[k]);
      case "net_usd": return money(t.net_usd);
      case "r": return rr(t.r);
      case "commission_usd": return money(-t.commission_usd);
      case "style": return `(${t.style})`;
      case "entry_time": return t.entry_time.slice(0, 5);
      default: return String(t[k]);
    }
  };
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <label className="flex h-8 w-64 items-center gap-2 rounded-sm border border-line-strong bg-bg-2 px-2.5 text-text-3 focus-within:border-cream/60">
          <Search className="size-3.5" aria-hidden="true" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by date, account, direction" aria-label="Filter trades" className="cf-bare-focus w-full bg-transparent text-[13px] text-text-1 outline-none placeholder:text-text-3" />
        </label>
        <span className="font-mono text-[11px] text-text-3">{list.length} trades · {label}</span>
        <div className="relative ml-auto">
          <button type="button" onClick={() => setPick((v) => !v)} aria-expanded={pick} className="flex h-8 items-center gap-2 rounded-sm border border-line-strong bg-bg-2 px-2.5 text-[13px] text-text-1">
            <Columns3 className="size-3.5 text-text-3" aria-hidden="true" />Columns</button>
          {pick && (
            <div className="absolute right-0 z-30 mt-1 w-48 rounded-md border border-line-strong bg-bg-3 p-1.5 shadow-[0_12px_32px_rgba(0,0,0,.55)]" data-column-chooser>
              {COLS.map((c) => (
                <label key={c.key} className="flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1 text-[13px] text-text-1 hover:bg-white/[.04]">
                  <input type="checkbox" checked={shown.has(c.key)} onChange={() => setShown((s) => { const n = new Set(s); if (n.has(c.key)) n.delete(c.key); else n.add(c.key); return n; })} />{c.label}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>
      <div ref={box} onScroll={(e) => setScroll(e.currentTarget.scrollTop)} className="max-h-[440px] overflow-auto rounded-md border border-line-strong/70" data-trades-table>
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 z-10 bg-bg-2">
            <tr>{cols.map((c) => {
              const on = sort.key === c.key;
              return (
                <th key={c.key} aria-sort={on ? (sort.asc ? "ascending" : "descending") : "none"} className={cn("border-b border-line-strong px-3 py-2 font-medium text-text-2", c.num ? "text-right" : "text-left")}>
                  <button type="button" className={cn("inline-flex items-center gap-1 hover:text-text-1", on && "text-text-1")} onClick={() => setSort({ key: c.key, asc: on ? !sort.asc : !c.num })}>
                    {c.label}{on ? (sort.asc ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />) : <ArrowUpDown className="size-3 opacity-40" />}
                  </button>
                </th>
              );
            })}</tr>
          </thead>
          <tbody className="font-mono">
            {first > 0 && <tr aria-hidden="true" style={{ height: first * ROW_H }} />}
            {list.slice(first, last).map((t) => (
              <tr key={t.id} className="border-b border-line-strong/30 hover:bg-bg-3/60" style={{ height: ROW_H }}>
                {cols.map((c) => (
                  <td key={c.key} className={cn("px-3", c.num && "text-right", (c.key === "net_usd" || c.key === "r") && pnlClass(t[c.key]), c.key === "size" && "font-sans text-text-2")}>{cell(t, c.key)}</td>
                ))}
              </tr>
            ))}
            {last < list.length && <tr aria-hidden="true" style={{ height: (list.length - last) * ROW_H }} />}
          </tbody>
        </table>
        {!list.length && <p className="px-3 py-6 text-center text-[13px] text-text-2">No trades to show.</p>}
      </div>
    </div>
  );
}
