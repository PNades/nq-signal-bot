"use client";
// Command Palette: 21st.dev id 23522 (ddoemonn), code read 2026-11-15: no network calls, no remote assets, no text
// addressed to an AI. CreamFlow changes: the Cocoa level-3/4 surfaces and tokens instead of the stone palette; items
// carry a `group` shown as a mono eyebrow hint and an optional `danger` flag (kill/flat are listed but still open
// their normal confirm, pages/shell.md); reduced motion follows the panel's Effects (`animated`) as well as the OS;
// the layer sits on the dialog z-level; `⌘` never shown (Windows).
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { cn } from "@/lib/utils";

const CELL = { type: "spring", stiffness: 520, damping: 34, mass: 0.45 } as const;
const CROSSFADE = { type: "spring", stiffness: 260, damping: 34, mass: 0.8 } as const;
const BOUNDARY = /[\s\-_/.:]/;
const ROW = 36;
const GAP = 2;
const PAD = 5;

export type CommandItem = {
  id: string;
  label: string;
  hint?: string;
  keywords?: string;
  shortcut?: string[];
  icon?: React.ReactNode;
  danger?: boolean;
  disabled?: boolean;
};

function scoreOne(text: string, query: string): number {
  const t = text.toLowerCase();
  let cursor = 0, total = 0, streak = 0;
  for (let i = 0; i < query.length; i++) {
    const at = t.indexOf(query[i], cursor);
    if (at < 0) return -1;
    streak = at === cursor && i > 0 ? streak + 1 : 0;
    total += 2 + streak * 4;
    if (at === 0) total += 12;
    else if (BOUNDARY.test(t[at - 1])) total += 8;
    cursor = at + 1;
  }
  return total;
}

function rank(items: CommandItem[], query: string): CommandItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  const scored: { item: CommandItem; score: number; order: number }[] = [];
  items.forEach((item, i) => {
    const direct = scoreOne(item.label, q);
    const aliased = item.keywords ? scoreOne(item.keywords, q) - 3 : -1;
    const best = Math.max(direct, aliased);
    if (best >= 0) scored.push({ item, score: best - item.label.length * 0.05, order: i });
  });
  scored.sort((a, b) => b.score - a.score || a.order - b.order);
  return scored.map((s) => s.item);
}

export function CommandPalette({ items, onSelect, onDismiss, open, animated = true, placeholder = "Search pages, accounts and actions", emptyLabel = "Nothing matches", label = "Command palette", maxRows = 8 }: {
  items: CommandItem[]; onSelect: (item: CommandItem) => void; onDismiss: () => void; open: boolean; animated?: boolean;
  placeholder?: string; emptyLabel?: string; label?: string; maxRows?: number;
}) {
  const uid = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const liveRef = useRef<HTMLSpanElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const pointer = useRef({ x: -1, y: -1 });
  const [query, setQuery] = useState("");
  const [pinned, setPinned] = useState<string | null>(null);
  const results = useMemo(() => rank(items, query), [items, query]);
  const activeId = results.some((r) => r.id === pinned) ? pinned : (results.find((r) => !r.disabled)?.id ?? null);
  const activeIndex = results.findIndex((r) => r.id === activeId);
  const count = results.length;
  const rows = Math.max(1, Math.min(maxRows, items.length));
  const height = PAD * 2 + rows * ROW + (rows - 1) * GAP;
  const spring = animated ? CELL : { duration: 0 };

  useEffect(() => { if (open) { setQuery(""); setPinned(null); requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true })); } }, [open]);
  useEffect(() => { if (listRef.current) listRef.current.scrollTop = 0; }, [query]);
  useEffect(() => {
    const id = setTimeout(() => { if (liveRef.current) liveRef.current.textContent = count === 0 ? emptyLabel : `${count} ${count === 1 ? "result" : "results"}`; }, 400);
    return () => clearTimeout(id);
  }, [count, emptyLabel]);

  const reveal = (index: number) => {
    const list = listRef.current;
    const row = list?.children[index];
    if (!list || !(row instanceof HTMLElement)) return;
    const top = row.offsetTop - PAD, bottom = row.offsetTop + row.offsetHeight + PAD;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  };
  const jump = (index: number) => {
    if (!results.length) return;
    const next = Math.max(0, Math.min(results.length - 1, index));
    setPinned(results[next].id);
    reveal(next);
  };
  const move = (delta: number) => { if (results.length) jump(((activeIndex < 0 ? 0 : activeIndex) + delta + results.length) % results.length); };
  const run = (item?: CommandItem) => {
    const target = item ?? results.find((r) => r.id === activeId);
    if (target && !target.disabled) onSelect(target);
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (e.key === "Home") { e.preventDefault(); jump(0); }
    else if (e.key === "End") { e.preventDefault(); jump(results.length - 1); }
    else if (e.key === "Enter") { e.preventDefault(); run(); }
    else if (e.key === "Escape") { e.preventDefault(); onDismiss(); }
  };

  const surface = (
    <div ref={surfaceRef} className="w-full max-w-[560px] overflow-hidden rounded-lg border border-line-strong bg-bg-3 shadow-[0_24px_64px_rgba(0,0,0,.7)]">
      <div className="flex h-11 items-center gap-2.5 border-b border-white/[.07] px-3">
        <svg viewBox="0 0 16 16" className="size-[14px] shrink-0 text-text-2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden>
          <circle cx="7" cy="7" r="4.25" /><path d="M10.2 10.2 13.5 13.5" />
        </svg>
        <input ref={inputRef} type="text" role="combobox" aria-label={label} aria-expanded aria-controls={`${uid}-list`} aria-autocomplete="list"
          aria-activedescendant={activeId ? `${uid}-${activeId}` : undefined} autoComplete="off" spellCheck={false}
          value={query} placeholder={placeholder} onChange={(e) => setQuery(e.target.value)} onKeyDown={onKeyDown}
          className="cf-bare-focus h-full min-w-0 flex-1 bg-transparent text-[14px] text-text-1 outline-none placeholder:text-text-3" />
        <span className="min-w-[3ch] shrink-0 text-right font-mono text-[11px] text-text-3">{count}</span>
      </div>
      <div className="relative" style={{ height }}>
        <ul ref={listRef} id={`${uid}-list`} role="listbox" aria-label={label} onMouseDown={(e) => e.preventDefault()}
          className="absolute inset-0 flex flex-col gap-[2px] overflow-y-auto overscroll-contain p-[5px] [scrollbar-gutter:stable]">
          {results.map((item) => {
            const active = item.id === activeId;
            return (
              <motion.li key={item.id} id={`${uid}-${item.id}`} role="option" aria-selected={active} aria-disabled={item.disabled || undefined}
                layout={animated ? "position" : false} transition={spring}
                onPointerMove={(e) => {
                  const { x, y } = pointer.current;
                  if (e.clientX === x && e.clientY === y) return;
                  pointer.current = { x: e.clientX, y: e.clientY };
                  if (item.id !== activeId) setPinned(item.id);
                }}
                onClick={() => run(item)}
                className={cn("relative flex h-9 shrink-0 cursor-default items-center rounded-sm px-2.5", item.disabled && "opacity-45")}>
                <motion.span aria-hidden initial={false} animate={{ opacity: active ? 1 : 0 }} transition={animated ? CROSSFADE : { duration: 0 }}
                  className={cn("absolute inset-0 rounded-sm", item.danger ? "bg-crit/12" : "bg-cream/10")} />
                <span className="relative flex min-w-0 flex-1 items-center gap-2.5">
                  {item.icon && <span className={cn("shrink-0 [&>svg]:size-4", item.danger ? "text-crit" : "text-text-2")}>{item.icon}</span>}
                  <span className={cn("truncate text-[13.5px] font-medium", item.danger ? "text-crit" : "text-text-1")}>{item.label}</span>
                  {item.hint && <span className="hidden shrink-0 text-[12px] text-text-3 sm:inline">{item.hint}</span>}
                  {item.shortcut && (
                    <span className="ml-auto flex shrink-0 items-center gap-1">
                      {item.shortcut.map((key) => (
                        <span key={key} className="flex h-[18px] min-w-[18px] items-center justify-center rounded-xs border border-line-strong px-1 font-mono text-[10.5px] text-text-2">{key}</span>
                      ))}
                    </span>
                  )}
                </span>
              </motion.li>
            );
          })}
        </ul>
        {count === 0 && (
          <p className="pointer-events-none absolute inset-0 flex items-center justify-center px-3 text-center text-[13px] text-text-2">{emptyLabel}</p>
        )}
      </div>
      <span ref={liveRef} role="status" aria-live="polite" className="sr-only" />
    </div>
  );

  return createPortal(
    <AnimatePresence>
      {open && (
        // the panel's top sits so the open palette is centred slightly above the middle of the window, and stays put while
        // typing (the list keeps a fixed height; owner 2026-11-15)
        <motion.div key="palette-layer" className="fixed inset-0 z-[50] flex items-start justify-center px-4 pt-[max(16px,calc(46vh-190px))]" role="dialog" aria-modal="true" aria-label={label}
          initial="closed" animate="open" exit="gone" variants={{ closed: {}, open: {}, gone: {} }}
          // a click anywhere outside the palette closes it, as Esc does (owner 2026-11-15); the backdrop and the full-width
          // wrapper both count as outside
          onPointerDown={(e) => { if (!surfaceRef.current?.contains(e.target as Node)) { e.preventDefault(); onDismiss(); } }}>
          <motion.div aria-hidden className="absolute inset-0 bg-black/60 backdrop-blur-[8px]"
            variants={{ closed: { opacity: 0 }, open: { opacity: 1, transition: animated ? { duration: 0.2 } : { duration: 0 } }, gone: { opacity: 0, transition: animated ? { duration: 0.15 } : { duration: 0 } } }} />
          <motion.div className="relative flex w-full justify-center"
            variants={{
              closed: animated ? { opacity: 0, scale: 0.97, y: 10 } : { opacity: 0 },
              open: { opacity: 1, scale: 1, y: 0, transition: animated ? { type: "spring", stiffness: 420, damping: 36 } : { duration: 0 } },
              gone: animated ? { opacity: 0, scale: 0.98, y: 6, transition: { duration: 0.15 } } : { opacity: 0, transition: { duration: 0 } },
            }}>
            {surface}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
