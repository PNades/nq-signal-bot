// The P&L calendar's month picker (owner 2026-11-16): our own dropdown in place of the browser's <select>, modelled on
// the top-bar account switcher (shell/AccountSwitcher.tsx: trigger + listbox, themed hover / selected states, the keys)
// and 21st.dev "Calendar with Custom Select Dropdown" (cnippet-dev, id 25124: a month list in a custom select) with the
// prev / next pair of "Pagination with Arrows" (sean0205, id 28336). The arrow sits next to the text; newest first; six
// months visible, the rest scroll; ↑ ↓ Home End move, Enter / Space pick, Esc closes; Calm: no motion.
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

const NAME = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
const label = (ym: string) => NAME.format(new Date(ym + "-15T12:00:00Z"));
const ROW_PX = 34;
const VISIBLE = 6;

export function MonthPicker({ value, months, onChange, onStep, animated }: {
  value: string; months: string[]; onChange: (ym: string) => void; onStep: (d: number) => void; animated: boolean;
}) {
  const list = [...new Set([...months, value])].sort().reverse();   // newest first
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  // opened by the keys: the current month is highlighted; by a click: nothing until the pointer or a key moves
  const show = (viaKeys = false) => { setActive(viaKeys ? Math.max(0, list.indexOf(value)) : -1); setOpen(true); requestAnimationFrame(() => panel.current?.focus()); };
  const hide = (refocus = true) => { setOpen(false); if (refocus) btn.current?.focus(); };
  const pick = (ym: string) => { onChange(ym); hide(); };
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { const t = e.target as Node; if (!panel.current?.contains(t) && !btn.current?.contains(t)) setOpen(false); };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);
  useEffect(() => {
    (panel.current?.querySelector(`[data-row-index="${active}"]`) as HTMLElement | null)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); hide(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => (i < 0 ? Math.max(0, list.indexOf(value)) : Math.min(list.length - 1, i + 1))); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => (i < 0 ? Math.max(0, list.indexOf(value)) : Math.max(0, i - 1))); }
    else if (e.key === "Home") { e.preventDefault(); setActive(0); }
    else if (e.key === "End") { e.preventDefault(); setActive(list.length - 1); }
    else if ((e.key === "Enter" || e.key === " ") && active >= 0) { e.preventDefault(); pick(list[active]); }
    else if (e.key === "Tab") setOpen(false);
  };
  const arrowBtn = "grid size-8 place-items-center rounded-sm border border-line-strong bg-bg-2 text-text-2 transition-colors hover:border-text-3 hover:text-text-1";
  return (
    <div className="relative flex items-center gap-1.5" data-month-picker>
      <button type="button" aria-label="Previous month" onClick={() => onStep(-1)} className={arrowBtn}><ChevronLeft className="size-4" aria-hidden="true" /></button>
      <button ref={btn} type="button" aria-haspopup="listbox" aria-expanded={open} aria-label={`Month: ${label(value)}`}
        onClick={() => (open ? hide(false) : show())}
        onKeyDown={(e) => { if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) { e.preventDefault(); show(true); } }}
        className={cn("flex h-8 items-center gap-2 rounded-sm border bg-bg-2 px-2.5 font-mono text-[12.5px] text-text-1 transition-colors hover:border-text-3", open ? "border-text-3" : "border-line-strong")}>
        <span>{label(value)}</span>
        <ChevronDown className={cn("size-3.5 text-text-3 transition-transform", open && "rotate-180")} aria-hidden="true" />
      </button>
      <button type="button" aria-label="Next month" onClick={() => onStep(1)} className={arrowBtn}><ChevronRight className="size-4" aria-hidden="true" /></button>
      <AnimatePresence>
        {open && (
          <motion.div ref={panel} tabIndex={-1} role="listbox" aria-label="Months" aria-activedescendant={active >= 0 ? `month-${list[active]}` : undefined}
            onKeyDown={onKey} onMouseLeave={() => setActive(-1)} data-month-list
            initial={animated ? { opacity: 0, y: -4 } : false} animate={{ opacity: 1, y: 0 }} exit={animated ? { opacity: 0, y: -4, transition: { duration: 0.12 } } : { opacity: 0, transition: { duration: 0 } }}
            transition={{ duration: animated ? 0.16 : 0 }}
            className="cf-bare-focus absolute right-10 top-full z-40 mt-1.5 w-[220px] overflow-y-auto rounded-md border border-line-strong bg-bg-3 py-1 shadow-[0_12px_32px_rgba(0,0,0,.55)] outline-none"
            style={{ maxHeight: VISIBLE * ROW_PX + 8 }}>
            {list.map((m, i) => (
              <div key={m} id={`month-${m}`} role="option" aria-selected={m === value} data-row-index={i} data-month={m}
                onClick={() => pick(m)} onMouseMove={() => setActive(i)}
                className={cn("mx-1 flex cursor-pointer items-center justify-between rounded-sm px-2.5 font-mono text-[12.5px] transition-colors",
                  i === active ? "bg-white/[.06] text-text-1" : "text-text-2", m === value && "text-cream")}
                style={{ height: ROW_PX }}>
                <span>{label(m)}</span>
                {m === value && <Check className="size-3.5 text-cream" aria-hidden="true" />}
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
