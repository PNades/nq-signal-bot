"use client";
// Preset picker (SPEC §9b item 1, pages/accounts.md: "searchable select grouped by firm"): the pattern of 21st.dev
// Combobox id 34727 (halaska-studio; code read 2026-11-15: no network calls) — a trigger button, a popup with a filter
// field, keyboard (arrows, Enter, Esc) — rebuilt on the CreamFlow tokens: its inline token palette and its undefined
// `halaska-scale-in` keyframe replaced by our classes, and options grouped under firm headings, each with its stage
// and a "saved" tag for the owner's own presets (Save as preset). Each of the owner's own presets has a delete control
// with a quick inline confirm (built-in firm presets can't be deleted; owner 2026-11-15); the page offers Undo after.
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface PresetOption { id: string; label: string; firm: string; stage: string; builtin: boolean }

export function PresetPicker({ presets, value, onPick, onDelete, placeholder = "Fill from a preset…" }: {
  presets: PresetOption[]; value: string | null; onPick: (id: string) => void; placeholder?: string;
  /** delete one of the owner's own presets (never offered for a built-in one) */
  onDelete?: (p: PresetOption) => void;
}) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [idx, setIdx] = useState(-1);
  const box = useRef<HTMLDivElement>(null);
  const uid = useId();
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return presets.filter((p) => !q || `${p.label} ${p.firm} ${p.stage}`.toLowerCase().includes(q));
  }, [presets, query]);
  const selected = presets.find((p) => p.id === value);
  useEffect(() => {
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);
  useEffect(() => { if (open) { setQuery(""); setConfirming(null); setIdx(-1); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (p: PresetOption) => { onPick(p.id); setOpen(false); (box.current?.querySelector("[data-trigger]") as HTMLElement | null)?.focus(); };
  const firms = Array.from(new Set(filtered.map((p) => p.firm)));
  return (
    <div ref={box} className="relative w-full max-w-[420px]">
      <button type="button" data-trigger role="combobox" aria-haspopup="listbox" aria-expanded={open} aria-controls={`${uid}-list`} onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => { if (e.key === "ArrowDown" && !open) { e.preventDefault(); setOpen(true); } }}
        className="flex h-9 w-full items-center justify-between gap-2 rounded-sm border border-line-strong bg-bg-2 px-3 text-left text-[13.5px] hover:border-text-3">
        <span className={cn("truncate", selected ? "text-text-1" : "text-text-3")}>
          {selected ? <>{selected.label} <span className="text-text-2">· {selected.stage}</span>{!selected.builtin && <span className="ml-1.5 font-mono text-[10.5px] text-gold">SAVED</span>}</> : placeholder}
        </span>
        <ChevronDown className={cn("size-4 shrink-0 text-text-3 transition-transform", open && "rotate-180")} aria-hidden="true" />
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-[calc(100%+4px)] z-[40] overflow-hidden rounded-md border border-line-strong bg-bg-3 shadow-[0_12px_32px_rgba(0,0,0,.55)]">
          <div className="flex items-center gap-2 border-b border-line-strong px-3 focus-within:border-cream">
            <Search className="size-3.5 text-text-3" aria-hidden="true" />
            <input autoFocus value={query} onChange={(e) => { setQuery(e.target.value); setIdx(0); }} placeholder="Filter presets" aria-label="Filter presets"
              aria-controls={`${uid}-list`} aria-activedescendant={filtered[idx] ? `${uid}-${filtered[idx].id}` : undefined}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(i + 1, filtered.length - 1)); }
                else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(i - 1, 0)); }
                else if (e.key === "Enter") { e.preventDefault(); if (filtered[idx]) pick(filtered[idx]); }
                else if (e.key === "Escape") { e.stopPropagation(); setOpen(false); }
              }}
              className="cf-bare-focus h-9 w-full bg-transparent text-[13px] text-text-1 outline-none placeholder:text-text-3" />
          </div>
          <div id={`${uid}-list`} role="listbox" className="max-h-[300px] overflow-y-auto p-1" onMouseLeave={() => setIdx(-1)}>
            {filtered.length === 0 && <div className="p-3 text-center text-[12.5px] text-text-3">No preset matches</div>}
            {firms.map((firm) => (
              <div key={firm} role="group" aria-label={firm}>
                <div className="eyebrow px-2.5 pb-1 pt-2">{firm}</div>
                {filtered.filter((p) => p.firm === firm).map((p) => {
                  const i = filtered.indexOf(p);
                  if (confirming === p.id) {
                    return (
                      <div key={p.id} className="flex items-center justify-between gap-2 rounded-sm bg-crit/10 px-2.5 py-1.5 text-[13px]" data-preset-confirm>
                        <span className="truncate text-text-1">Delete {p.label} · {p.stage}?</span>
                        <span className="flex shrink-0 items-center gap-1.5">
                          <button type="button" onClick={() => setConfirming(null)} className="h-7 rounded-sm border border-line-strong bg-bg-2 px-2.5 text-[12.5px] text-text-1 hover:border-text-3">Keep</button>
                          <button type="button" autoFocus onClick={() => { setConfirming(null); onDelete?.(p); }} className="h-7 rounded-sm bg-crit-fill px-2.5 text-[12.5px] font-semibold text-white hover:bg-[#e02a31]">Delete</button>
                        </span>
                      </div>
                    );
                  }
                  return (
                    <div key={p.id} className={cn("group flex items-center gap-1 rounded-sm", i === idx ? "bg-cream/10" : "")} onMouseEnter={() => setIdx(i)}>
                      <button id={`${uid}-${p.id}`} type="button" role="option" aria-selected={p.id === value} tabIndex={-1} onClick={() => pick(p)}
                        className="flex min-w-0 flex-1 items-center justify-between gap-2 px-2.5 py-1.5 text-left text-[13px]">
                        <span className="truncate text-text-1">{p.label} <span className="text-text-2">· {p.stage}</span></span>
                        <span className="flex shrink-0 items-center gap-1.5">
                          {!p.builtin && <span className="font-mono text-[10.5px] text-gold">SAVED</span>}
                          {p.id === value && <Check className="size-3.5 text-cream" aria-hidden="true" />}
                        </span>
                      </button>
                      {!p.builtin && onDelete && (
                        <button type="button" aria-label={`Delete preset ${p.label} (${p.stage})`} data-hint="Delete this preset" data-preset-delete
                          onClick={() => setConfirming(p.id)}
                          className="mr-1 grid size-7 shrink-0 place-items-center rounded-sm text-text-3 hover:bg-crit/10 hover:text-crit">
                          <Trash2 className="size-3.5" aria-hidden="true" />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
