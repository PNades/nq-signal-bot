// The panel's own dropdown (owner 2026-11-17: no browser <select> anywhere), the same pattern and styling as the
// Performance month picker (pages/performance/MonthPicker.tsx) and the top-bar account switcher: a trigger button and a
// themed listbox, optional group headings, newest-first lists as the caller orders them; ↑ ↓ Home End move, Enter /
// Space pick, Esc closes (focus back on the trigger), Tab leaves; a click outside closes; Calm: no motion.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export interface PickOption { value: string; label: ReactNode; note?: ReactNode; text?: string }
export interface PickGroup { label?: string; options: PickOption[] }

const ROW_PX = 34;

export function OptionPicker({ value, groups, onChange, ariaLabel, trigger, animated, visible = 8, width = 260, align = "left",
  autoOpen = false, onClose, className, dataAttr = "data-option-picker" }: {
  value: string; groups: PickGroup[]; onChange: (v: string) => void; ariaLabel: string; trigger: ReactNode; animated: boolean;
  visible?: number; width?: number; align?: "left" | "right"; autoOpen?: boolean; onClose?: () => void; className?: string;
  dataAttr?: string;
}) {
  const flat = groups.flatMap((g) => g.options);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const idBase = useRef(`opt-${Math.random().toString(36).slice(2, 8)}`).current;
  const show = (viaKeys = false) => {
    setActive(viaKeys ? Math.max(0, flat.findIndex((o) => o.value === value)) : -1); setOpen(true);
    requestAnimationFrame(() => panel.current?.focus({ preventScroll: true }));
  };
  // focus goes back to the trigger without scrolling it into view (a day switch keeps the scroll position, owner 2026-11-17)
  const hide = (refocus = true) => { setOpen(false); if (refocus) btn.current?.focus({ preventScroll: true }); onClose?.(); };
  const pick = (v: string) => { onChange(v); hide(); };
  useEffect(() => { if (autoOpen) show(true); /* opened by the caller (an inline edit) */ }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { const t = e.target as Node; if (!panel.current?.contains(t) && !btn.current?.contains(t)) { setOpen(false); onClose?.(); } };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open, onClose]);
  useEffect(() => {
    (panel.current?.querySelector(`[data-row-index="${active}"]`) as HTMLElement | null)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);
  const onKey = (e: React.KeyboardEvent) => {
    const cur = active < 0 ? Math.max(0, flat.findIndex((o) => o.value === value)) : active;
    if (e.key === "Escape") { e.preventDefault(); hide(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setActive(active < 0 ? cur : Math.min(flat.length - 1, active + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(active < 0 ? cur : Math.max(0, active - 1)); }
    else if (e.key === "Home") { e.preventDefault(); setActive(0); }
    else if (e.key === "End") { e.preventDefault(); setActive(flat.length - 1); }
    else if ((e.key === "Enter" || e.key === " ") && active >= 0) { e.preventDefault(); pick(flat[active].value); }
    else if (e.key === "Tab") { setOpen(false); onClose?.(); }
  };
  let row = -1;
  return (
    <div className={cn("relative", className)} {...{ [dataAttr]: "" }}>
      <button ref={btn} type="button" aria-haspopup="listbox" aria-expanded={open} aria-label={ariaLabel}
        onClick={() => (open ? hide(false) : show())}
        onKeyDown={(e) => { if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) { e.preventDefault(); show(true); } }}
        className={cn("flex h-8 items-center gap-2 rounded-sm border bg-bg-2 px-2.5 font-mono text-[12.5px] text-text-1 transition-colors hover:border-text-3", open ? "border-text-3" : "border-line-strong")}>
        {trigger}
        <ChevronDown className={cn("size-3.5 text-text-3 transition-transform", open && "rotate-180")} aria-hidden="true" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div ref={panel} tabIndex={-1} role="listbox" aria-label={ariaLabel} aria-activedescendant={active >= 0 ? `${idBase}-${active}` : undefined}
            onKeyDown={onKey} onMouseLeave={() => setActive(-1)} data-option-list
            initial={animated ? { opacity: 0, y: -4 } : false} animate={{ opacity: 1, y: 0 }}
            exit={animated ? { opacity: 0, y: -4, transition: { duration: 0.12 } } : { opacity: 0, transition: { duration: 0 } }}
            transition={{ duration: animated ? 0.16 : 0 }}
            className={cn("cf-bare-focus absolute top-full z-40 mt-1.5 overflow-y-auto rounded-md border border-line-strong bg-bg-3 py-1 shadow-[0_12px_32px_rgba(0,0,0,.55)] outline-none",
              align === "right" ? "right-0" : "left-0")}
            style={{ width, maxHeight: visible * ROW_PX + 8 }}>
            {groups.map((g, gi) => (
              <div key={gi} role="group" aria-label={g.label}>
                {g.label && <div className="eyebrow px-3.5 pb-1 pt-2 text-[10.5px]" aria-hidden="true">{g.label}</div>}
                {g.options.map((o) => {
                  row += 1;
                  const i = row;
                  return (
                    <div key={o.value} id={`${idBase}-${i}`} role="option" aria-selected={o.value === value} data-row-index={i} data-value={o.value}
                      onClick={() => pick(o.value)} onMouseMove={() => setActive(i)}
                      className={cn("mx-1 flex cursor-pointer items-center justify-between gap-2 rounded-sm px-2.5 font-mono text-[12.5px] transition-colors",
                        i === active ? "bg-white/[.06] text-text-1" : "text-text-2", o.value === value && "text-cream")}
                      style={{ minHeight: ROW_PX }}>
                      <span className="min-w-0 truncate">{o.label}</span>
                      <span className="flex shrink-0 items-center gap-1.5">{o.note}{o.value === value && <Check className="size-3.5 text-cream" aria-hidden="true" />}</span>
                    </div>
                  );
                })}
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
