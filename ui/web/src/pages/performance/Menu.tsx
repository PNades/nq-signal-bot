// The Performance page's small menu button (account, phase, account type): a trigger and a list that closes on a pick,
// an outside click or Esc; ← → on the closed trigger step through the options when `onStep` is given.
// Owner 2026-11-16: options may carry a `group` (a small heading above the first option of each group, e.g. "Archived"),
// and `searchable` puts a search field at the top of the list (the account menu, past 10 accounts; the pattern of 21st.dev
// Country Combobox id 26105, our own markup).
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown, Search } from "lucide-react";
import { cn } from "@/lib/utils";

// a menu row with its sub-label (py-1.5 + two lines)
const MENU_ROW_PX = 48;
const GROUP_HEADING_PX = 26;
// a long list never runs under the page dock: the room kept below it (dock and margin), the search field's height
const DOCK_RESERVE_PX = 104, SEARCH_PX = 41;

export function Menu<T extends string>({ value, options, onChange, icon, label, align = "right", onStep, animated, maxVisible, width, searchable = false }: {
  value: T; options: { id: T; name: string; sub?: string; group?: string }[]; onChange: (v: T) => void; icon: ReactNode; label: string;
  align?: "right" | "center" | "left"; onStep?: (d: number) => void; animated: boolean;
  /** show this many options at a time; the list scrolls past them */
  maxVisible?: number;
  /** a fixed trigger width (the header's menus keep their size whatever they show) */
  width?: number;
  /** a search field at the top of the list */
  searchable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const [room, setRoom] = useState<number | null>(null);
  useEffect(() => {
    if (!open) { setQuery(""); return; }
    const r = box.current?.getBoundingClientRect();
    setRoom(r ? Math.max(3 * MENU_ROW_PX, window.innerHeight - r.bottom - 4 - DOCK_RESERVE_PX - (searchable ? SEARCH_PX : 0)) : null);
    if (searchable) requestAnimationFrame(() => field.current?.focus());
  }, [open, searchable]);
  const q = query.trim().toLowerCase();
  const shown = q ? options.filter((o) => `${o.name} ${o.sub ?? ""} ${o.group ?? ""}`.toLowerCase().includes(q)) : options;
  const grouped = shown.some((o) => o.group);
  useEffect(() => {
    if (!open) return;
    const off = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", off); window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("pointerdown", off); window.removeEventListener("keydown", esc); };
  }, [open]);
  const cur = options.find((o) => o.id === value) ?? options[0];
  return (
    <div ref={box} className="relative">
      <button type="button" aria-haspopup="listbox" aria-expanded={open} aria-label={`${label}: ${cur?.name}`} onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => { if (!open && onStep && (e.key === "ArrowRight" || e.key === "ArrowLeft")) { e.preventDefault(); onStep(e.key === "ArrowRight" ? 1 : -1); } }}
        className="flex h-8 items-center gap-2 rounded-sm border border-line-strong bg-bg-2 px-2.5 text-[13px] text-text-1" style={width ? { width } : undefined}>{icon}<span className="min-w-0 flex-1 truncate text-left">{cur?.name}</span><ChevronDown className="size-3.5 shrink-0 text-text-3" aria-hidden="true" /></button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: animated ? 0.14 : 0 }}
            className={cn("absolute z-40 mt-1 w-[min(270px,calc(100vw-32px))] overflow-hidden rounded-md border border-line-strong bg-bg-3 shadow-[0_12px_32px_rgba(0,0,0,.55)]", align === "center" ? "left-1/2 -translate-x-1/2" : align === "left" ? "left-0" : "right-0")}
            data-menu-box>
            {searchable && (
              <label className="flex items-center gap-2 border-b border-line-strong px-3 py-1.5 focus-within:border-cream" data-menu-search>
                <Search className="size-3.5 shrink-0 text-text-3" aria-hidden="true" />
                <input ref={field} value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`Search ${label.toLowerCase()}s`} aria-label={`Search ${label.toLowerCase()}s`}
                  onKeyDown={(e) => { if (e.key === "Enter" && shown[0]) { e.preventDefault(); onChange(shown[0].id); setOpen(false); } }}
                  spellCheck={false} autoComplete="off" className="h-7 min-w-0 flex-1 bg-transparent text-[13px] text-text-1 outline-none placeholder:text-text-3" />
              </label>
            )}
            <ul role="listbox" aria-label={label} className={cn("m-0 list-none py-1", maxVisible && "overflow-y-auto overscroll-contain")}
              style={maxVisible ? { maxHeight: Math.min(maxVisible * MENU_ROW_PX + 8 + (grouped ? GROUP_HEADING_PX : 0), room ?? Infinity) } : undefined} data-menu-list>
              {shown.length === 0 && <li role="presentation" className="px-3 py-3 text-[13px] text-text-2" data-menu-empty>Nothing matches “{query}”.</li>}
              {shown.map((o, i) => (
                <li key={o.id} role="presentation">
                  {o.group && o.group !== shown[i - 1]?.group && (
                    <span className="flex items-end px-3 pb-1 font-mono text-[10.5px] uppercase tracking-[0.12em] text-text-3" style={{ height: GROUP_HEADING_PX }} data-menu-group={o.group}>{o.group}</span>
                  )}
                  <button type="button" role="option" aria-selected={o.id === value} onClick={() => { onChange(o.id); setOpen(false); }} className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-white/[.04]">
                    <span className="min-w-0 flex-1"><span className="block text-[13px] text-text-1">{o.name}</span>{o.sub && <span className="block text-[11.5px] text-text-3">{o.sub}</span>}</span>
                    {o.id === value && <Check className="size-3.5 text-gold" aria-hidden="true" />}
                  </button>
                </li>
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

