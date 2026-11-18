"use client";
// Keyboard Shortcuts: 21st.dev id 26919 (laziekiki), code read 2026-11-15: React + framer-motion only, no network
// calls, no remote assets. CreamFlow changes: Windows key names only (Ctrl, never ⌘; owner 2026-11-14); the Cocoa
// level-4 dialog surface; the dialog z-level (50) instead of 9998, so the critical layer stays above it; motion
// follows the panel's Effects (`animated`); an optional `footer` line (the emergency-page link).
import * as React from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";

export type Shortcut = { keys: string | string[]; label: string };
export type ShortcutGroup = { heading: string; items: Shortcut[] };

function glyph(token: string): string {
  const t = token.trim().toLowerCase();
  const map: Record<string, string> = { mod: "Ctrl", ctrl: "Ctrl", shift: "Shift", alt: "Alt", enter: "Enter", esc: "Esc", space: "Space", up: "↑", down: "↓", left: "←", right: "→" };
  if (map[t]) return map[t];
  return token.length === 1 ? token.toUpperCase() : token.charAt(0).toUpperCase() + token.slice(1);
}
const parse = (combo: string) => combo.trim().split("+").map(glyph);
const alts = (keys: string | string[]) => (Array.isArray(keys) ? keys : [keys]);

function Cap({ label }: { label: string }) {
  return (
    <kbd className={"inline-flex h-[22px] items-center justify-center rounded-xs border border-line-strong bg-bg-2 font-mono text-[11px] leading-none text-text-2 " + (label.length <= 1 ? "w-[22px]" : "px-1.5")}>
      {label}
    </kbd>
  );
}

export function ShortcutsDialog({ groups, open, onOpenChange, animated = true, title = "Keyboard shortcuts", footer }: {
  groups: ShortcutGroup[]; open: boolean; onOpenChange: (open: boolean) => void; animated?: boolean; title?: string; footer?: React.ReactNode;
}) {
  const [query, setQuery] = React.useState("");
  const rootRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (!open) return;
    setQuery("");
    const t = window.setTimeout(() => inputRef.current?.focus(), 40);
    return () => window.clearTimeout(t);
  }, [open]);
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onOpenChange(false); return; }
      if (e.key !== "Tab") return;
      const f = rootRef.current?.querySelectorAll<HTMLElement>('button, [href], input, [tabindex]:not([tabindex="-1"])');
      if (!f || f.length === 0) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);
  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups.map((g) => ({ ...g, items: g.items.filter((it) => it.label.toLowerCase().includes(q) || alts(it.keys).join(" ").toLowerCase().includes(q) || g.heading.toLowerCase().includes(q)) }))
      .filter((g) => g.items.length > 0);
  }, [groups, query]);
  const t = (d: number) => ({ duration: animated ? d : 0 });
  return createPortal(
    <div ref={rootRef}>
      <AnimatePresence>
        {open && (
          <motion.div className="fixed inset-0 z-[50] flex items-start justify-center overflow-y-auto p-4 pt-[12vh]" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={t(0.16)}>
            <div className="fixed inset-0 bg-black/60 backdrop-blur-[8px]" onClick={() => onOpenChange(false)} aria-hidden />
            <motion.div role="dialog" aria-modal="true" aria-label={title}
              className="relative z-[1] w-full max-w-lg overflow-hidden rounded-lg border border-line-strong bg-bg-3 text-text-1 shadow-[0_24px_64px_rgba(0,0,0,.7)]"
              initial={animated ? { opacity: 0, y: 8, scale: 0.98 } : { opacity: 0 }} animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={animated ? { opacity: 0, y: 8, scale: 0.98 } : { opacity: 0 }}
              transition={animated ? { type: "spring", stiffness: 360, damping: 30, mass: 0.7 } : { duration: 0 }}>
              <div className="flex items-center justify-between gap-3 border-b border-line-strong px-4 py-3">
                <h2 className="text-[14px] font-semibold">{title}</h2>
                <button onClick={() => onOpenChange(false)} aria-label="Close" className="grid size-7 place-items-center rounded-sm text-text-2 hover:bg-bg-2 hover:text-text-1">
                  <X className="size-4" aria-hidden="true" />
                </button>
              </div>
              <div className="flex items-center gap-2 border-b border-line-strong px-4 py-2.5 focus-within:border-cream">
                <input ref={inputRef} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search shortcuts" aria-label="Search shortcuts"
                  className="w-full bg-transparent text-[13px] text-text-1 placeholder:text-text-3 focus:outline-none" />
              </div>
              <div className="max-h-[56vh] overflow-y-auto px-3 py-1.5">
                {filtered.length === 0 ? (
                  <p className="px-2 py-10 text-center text-[13px] text-text-2">No shortcuts match "{query}".</p>
                ) : filtered.map((group, gi) => (
                  <div key={group.heading} className={gi === 0 ? "" : "mt-1"}>
                    <p className="eyebrow px-2 pb-1 pt-3">{group.heading}</p>
                    <ul>
                      {group.items.map((item) => (
                        <li key={item.label} className="flex items-center justify-between gap-4 rounded-sm px-2 py-[7px] hover:bg-bg-2">
                          <span className="truncate text-[13px] text-text-1">{item.label}</span>
                          <span className="flex shrink-0 items-center gap-2">
                            {alts(item.keys).map((combo, ai) => (
                              <React.Fragment key={combo}>
                                {ai > 0 && <span className="text-[11px] text-text-3">or</span>}
                                <span className="flex items-center gap-[3px]">{parse(combo).map((k, ki) => <Cap key={k + ki} label={k} />)}</span>
                              </React.Fragment>
                            ))}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between gap-3 border-t border-line-strong px-4 py-2.5 text-[12px] text-text-2">
                <span className="flex items-center gap-1.5">Press <Cap label="?" /> anywhere to open this</span>
                {footer ?? <span className="flex items-center gap-1.5"><Cap label="Esc" /> to close</span>}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>,
    document.body,
  );
}
