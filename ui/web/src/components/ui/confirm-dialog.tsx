"use client";
// Destructive confirm dialog (the KILL confirm): 21st.dev Alert Dialog Layout id 28277 (ai2), its "split" layout made
// richer ("band", the owner's pick 2026-11-15 over two other variants); code read 2026-11-15: self-contained (no Radix,
// no portal library), no network calls, no remote assets. CreamFlow changes (MASTER §6, §7, §10; owner 2026-11-15):
// - focus goes to the confirm button on open, so Enter confirms at once; focus stays inside: a click on the card's text
//   or empty space leaves it where it was (KILL by default), Tab cycles Cancel / KILL;
// - Esc closes it whenever it is open, wherever focus is (a window-level listener in the capture phase); Cancel and
//   KILL close it; a click outside does nothing (closing on an outside click is the command palette's, not this);
// - it appears at once in Full and Calm (risk dialogs never animate in, MASTER §7);
// - a red gradient band with a solid red badge and the Instrument Serif title, a thin red edge and a soft red glow on
//   the card, a dark blurred backdrop, the list of what the action does with icons and live counts, Esc / Enter key
//   chips on the buttons; it sits just above the vertical centre; the critical z-layer (70), above the top bar.
import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

export interface ConfirmItem { icon: ReactNode; label: ReactNode; count?: ReactNode }

function Keycap({ k, onRed = false }: { k: string; onRed?: boolean }) {
  return (
    <kbd className={cn("rounded-xs border px-1.5 font-mono text-[10.5px] font-normal leading-[18px]",
      onRed ? "border-white/35 bg-white/10 text-white/90" : "border-line-strong bg-bg-1 text-text-2")}>{k}</kbd>
  );
}

export function ConfirmDialog({ open, onOpenChange, onConfirm, icon, eyebrow, title, titleItalic, description, items = [], note, confirmLabel, cancelLabel = "Cancel" }: {
  open: boolean; onOpenChange: (open: boolean) => void; onConfirm: () => void;
  icon: ReactNode; eyebrow?: string; title: ReactNode; titleItalic?: ReactNode; description?: ReactNode; items?: ConfirmItem[];
  note?: ReactNode; confirmLabel: ReactNode; cancelLabel?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const opener = useRef<Element | null>(null);
  const close = useRef(onOpenChange);
  close.current = onOpenChange;

  useLayoutEffect(() => {
    if (!open) return;
    opener.current = document.activeElement;
    confirmRef.current?.focus();
    return () => { (opener.current as HTMLElement | null)?.focus?.({ preventScroll: true }); };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); close.current(false); return; }
      const f = panelRef.current ? Array.from(panelRef.current.querySelectorAll<HTMLElement>("button")) : [];
      if (e.key === "Tab" && f.length) {
        // focus never leaves the dialog: Tab / Shift+Tab cycle its buttons, from wherever focus is
        e.preventDefault();
        const i = f.indexOf(document.activeElement as HTMLElement);
        f[(i < 0 ? f.length - 1 : i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
        return;
      }
      // focus somewhere outside the dialog (it shouldn't be): put it back on the confirm button first
      if (!panelRef.current?.contains(document.activeElement)) confirmRef.current?.focus();
    };
    // focus that escapes comes straight back to the confirm button
    const onFocusIn = (e: FocusEvent) => { if (!panelRef.current?.contains(e.target as Node)) confirmRef.current?.focus(); };
    window.addEventListener("keydown", onKey, true);
    document.addEventListener("focusin", onFocusIn, true);
    return () => { window.removeEventListener("keydown", onKey, true); document.removeEventListener("focusin", onFocusIn, true); };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-[70]" data-confirm-dialog>
      {/* a click outside does nothing (only Cancel, Esc or KILL close it), and it never takes focus away either */}
      <div className="absolute inset-0 bg-black/70 backdrop-blur-[10px]" aria-hidden="true" data-confirm-backdrop onMouseDown={(e) => e.preventDefault()} />
      {/* the extra bottom padding lifts the card ~26 px above the vertical centre (owner 2026-11-15) */}
      <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-4 pb-[calc(1rem+52px)]">
        <div ref={panelRef} role="alertdialog" aria-modal="true" aria-labelledby="cf-confirm-title" aria-describedby="cf-confirm-desc"
          // a click on the card's text or empty space leaves focus where it is (KILL by default); buttons still take it
          onMouseDown={(e) => { if (!(e.target as HTMLElement).closest("button")) e.preventDefault(); }}
          className={cn("pointer-events-auto relative w-full max-w-[540px] overflow-hidden rounded-lg border border-crit/35 bg-bg-2 text-text-1",
            "shadow-[0_0_0_1px_rgba(255,79,79,.08),0_0_60px_-10px_rgba(212,35,42,.35),0_28px_70px_rgba(0,0,0,.75)]")}>
          <div className="relative flex items-center gap-4 border-b border-crit/30 bg-[linear-gradient(135deg,rgba(212,35,42,.30),rgba(212,35,42,.08)_60%,transparent)] px-6 py-5">
            <span className="grid size-11 shrink-0 place-items-center rounded-md bg-crit-fill text-white shadow-[0_6px_18px_rgba(212,35,42,.45)] [&_svg]:size-6" aria-hidden="true">{icon}</span>
            <div className="min-w-0">
              {eyebrow && <div className="eyebrow text-text-2">{eyebrow}</div>}
              <h2 id="cf-confirm-title" className="m-0 mt-1.5 font-display text-[28px] font-normal leading-[1.05] tracking-[-0.01em] text-text-1">
                {title}{titleItalic && <> <span className="italic text-cream">{titleItalic}</span></>}
              </h2>
            </div>
          </div>
          <div className="flex flex-col gap-4 px-6 pb-6 pt-5" id="cf-confirm-desc">
            {description && <p className="m-0 text-[14px] leading-5 text-text-2">{description}</p>}
            <ul className="m-0 flex list-none flex-col gap-3 p-0">
              {items.map((it, i) => (
                <li key={i} className="flex items-start gap-3">
                  <span className="mt-0.5 text-crit [&_svg]:size-[18px]" aria-hidden="true">{it.icon}</span>
                  <span className="min-w-0 flex-1 text-[13.5px] leading-5 text-text-1">{it.label}
                    {it.count !== undefined && <span className="ml-2 rounded-xs border border-line-strong px-1.5 py-px font-mono text-[11.5px] text-text-2">{it.count}</span>}
                  </span>
                </li>
              ))}
            </ul>
            {note && <p className="m-0 rounded-sm border border-line-strong/70 bg-bg-0/60 px-3 py-2 text-[12.5px] leading-[18px] text-text-2">{note}</p>}
            <div className="flex items-center justify-end gap-2.5 pt-1">
              <button type="button" onClick={() => onOpenChange(false)} data-cancel
                className="inline-flex h-10 items-center justify-center gap-2.5 rounded-sm border border-line-strong bg-bg-1 px-4 text-[13.5px] text-text-1 hover:border-text-3">
                {cancelLabel}<Keycap k="Esc" />
              </button>
              <button ref={confirmRef} type="button" onClick={() => { onConfirm(); onOpenChange(false); }} data-confirm
                className={cn("inline-flex h-10 items-center justify-center gap-2.5 rounded-sm bg-crit-fill px-5 text-[13.5px] font-semibold tracking-[0.05em] text-white",
                  "shadow-[0_0_0_1px_rgba(255,255,255,.1)_inset,0_6px_20px_rgba(212,35,42,.35)] hover:bg-[#e02a31]",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white")}>
                {confirmLabel}<Keycap k="Enter" onRed />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
