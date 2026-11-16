// The Performance page's small menu button (account, phase, account type): a trigger and a list that closes on a pick,
// an outside click or Esc; ← → on the closed trigger step through the options when `onStep` is given.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

// a menu row with its sub-label (py-1.5 + two lines)
const MENU_ROW_PX = 48;

export function Menu<T extends string>({ value, options, onChange, icon, label, align = "right", onStep, animated, maxVisible, width }: {
  value: T; options: { id: T; name: string; sub?: string }[]; onChange: (v: T) => void; icon: ReactNode; label: string;
  align?: "right" | "center" | "left"; onStep?: (d: number) => void; animated: boolean;
  /** show this many options at a time; the list scrolls past them */
  maxVisible?: number;
  /** a fixed trigger width (the header's menus keep their size whatever they show) */
  width?: number;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
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
          <motion.ul role="listbox" aria-label={label} initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
            transition={{ duration: animated ? 0.14 : 0 }}
            className={cn("absolute z-40 mt-1 w-[min(270px,calc(100vw-32px))] overflow-hidden rounded-md border border-line-strong bg-bg-3 py-1 shadow-[0_12px_32px_rgba(0,0,0,.55)]", align === "center" ? "left-1/2 -translate-x-1/2" : align === "left" ? "left-0" : "right-0", maxVisible && "overflow-y-auto")}
            style={maxVisible ? { maxHeight: maxVisible * MENU_ROW_PX + 8 } : undefined} data-menu-list>
            {options.map((o) => (
              <li key={o.id} role="option" aria-selected={o.id === value}>
                <button type="button" onClick={() => { onChange(o.id); setOpen(false); }} className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-white/[.04]">
                  <span className="min-w-0 flex-1"><span className="block text-[13px] text-text-1">{o.name}</span>{o.sub && <span className="block text-[11.5px] text-text-3">{o.sub}</span>}</span>
                  {o.id === value && <Check className="size-3.5 text-gold" aria-hidden="true" />}
                </button>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  );
}

