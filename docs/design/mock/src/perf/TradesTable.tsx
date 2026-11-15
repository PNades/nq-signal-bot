// Trades table (pages/performance.md): sortable, filterable, sticky header, mono numbers right-aligned. Pattern from
// 21st.dev shadcn Data Table (1050) and the aria-sort header pattern of 33506; written without tanstack for the mock.
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Search } from "lucide-react";
import { type Trade } from "./data";
import { money, pnlClass, rr } from "@/effects";
import { cn } from "@/lib/utils";

type Key = "date" | "time" | "account" | "dir" | "style" | "size" | "entry" | "exit" | "usd" | "r";
const COLS: { key: Key; label: string; num?: boolean }[] = [
  { key: "date", label: "Date" }, { key: "time", label: "Time" }, { key: "account", label: "Account" }, { key: "dir", label: "Dir" },
  { key: "style", label: "Style" }, { key: "size", label: "Size" }, { key: "entry", label: "Entry", num: true }, { key: "exit", label: "Exit", num: true },
  { key: "usd", label: "Net $", num: true }, { key: "r", label: "Net R", num: true },
];
const px = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function TradesTable({ trades: TRADES }: { trades: Trade[] }) {
  const [sort, setSort] = useState<{ key: Key; asc: boolean }>({ key: "date", asc: false });
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const f = q.trim().toLowerCase();
    const list = TRADES.filter((t) => !f || `${t.date} ${t.account} ${t.dir} ${t.style}`.toLowerCase().includes(f));
    return [...list].sort((a: Trade, b: Trade) => {
      const x = a[sort.key], y = b[sort.key];
      const c = typeof x === "number" ? (x as number) - (y as number) : String(x).localeCompare(String(y)) || a.time.localeCompare(b.time);
      return sort.asc ? c : -c;
    });
  }, [sort, q, TRADES]);
  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <label className="flex h-8 w-64 items-center gap-2 rounded-sm border border-line-strong bg-bg-2 px-2.5 text-text-3 focus-within:border-cream/60">
          <Search className="size-3.5" aria-hidden="true" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by date, account, direction" aria-label="Filter trades" className="w-full bg-transparent text-[13px] text-text-1 outline-none placeholder:text-text-3" />
        </label>
        <span className="font-mono text-[11px] text-text-3">{rows.length} trades · backtest · mock data</span>
      </div>
      <div className="max-h-[440px] overflow-auto rounded-md border border-line-strong/70">
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 z-10 bg-bg-2">
            <tr>{COLS.map((c) => {
              const on = sort.key === c.key;
              return (
                <th key={c.key} aria-sort={on ? (sort.asc ? "ascending" : "descending") : "none"} className={cn("border-b border-line-strong px-3 py-2 font-medium text-text-2", c.num ? "text-right" : "text-left")}>
                  <button className={cn("inline-flex items-center gap-1 hover:text-text-1", on && "text-text-1")} onClick={() => setSort({ key: c.key, asc: on ? !sort.asc : c.num ? false : true })}>
                    {c.label}{on ? (sort.asc ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />) : <ArrowUpDown className="size-3 opacity-40" />}
                  </button>
                </th>
              );
            })}</tr>
          </thead>
          <tbody className="font-mono">
            {rows.slice(0, 120).map((t) => (
              <tr key={t.id} className="border-b border-line-strong/30 hover:bg-bg-3/60">
                <td className="px-3 py-1.5">{t.date}</td><td className="px-3">{t.time}</td><td className="px-3">{t.account}</td><td className="px-3">{t.dir}</td>
                <td className="px-3">({t.style})</td><td className="px-3 font-sans text-text-2">{t.size}</td>
                <td className="px-3 text-right">{px.format(t.entry)}</td><td className="px-3 text-right">{px.format(t.exit)}</td>
                <td className={cn("px-3 text-right", pnlClass(t.usd))}>{money(t.usd)}</td><td className={cn("px-3 text-right", pnlClass(t.r))}>{rr(t.r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
