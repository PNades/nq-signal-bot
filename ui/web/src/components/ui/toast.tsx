// Toasts (MASTER §10): non-critical only, 4 s, bottom right, never under the dock (`--toast-bottom`, set by the shell).
// Built in the pattern of 21st.dev Sonner Toast id 27363 (isaiahbjork; code read 2026-11-15): a stack of small
// cards with a title and a one-line description. Not the `sonner` package itself: it inserts a <style> element at
// runtime, which the panel's CSP (`default-src 'self'`) blocks, so the styles live here in the bundle. Critical
// alerts are never toasts: they are banners that stay until acknowledged (components/ui/banner.tsx).
import { useEffect, useSyncExternalStore } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CircleCheck, Info, TriangleAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { notify } from "@/lib/notify";

export type ToastKind = "info" | "ok" | "warn";
interface ToastItem { id: number; kind: ToastKind; title: string; description?: string; ms: number }

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function toast(title: string, opts: { description?: string; kind?: ToastKind; ms?: number } = {}) {
  // every toast also goes into the bell's history (lib/notify.ts), as a non-banner note
  notify({ kind: opts.kind === "warn" ? "warn" : "info", title, text: opts.description, banner: false });
  items = [...items.slice(-3), { id: nextId++, kind: opts.kind ?? "info", title, description: opts.description, ms: opts.ms ?? 4000 }];
  emit();
}
const dismiss = (id: number) => { items = items.filter((t) => t.id !== id); emit(); };

const ICON = { info: Info, ok: CircleCheck, warn: TriangleAlert } as const;
const TONE = { info: "text-info", ok: "text-ok", warn: "text-warn" } as const;

function ToastCard({ t, animated }: { t: ToastItem; animated: boolean }) {
  useEffect(() => { const id = setTimeout(() => dismiss(t.id), t.ms); return () => clearTimeout(id); }, [t.id, t.ms]);
  const Icon = ICON[t.kind];
  return (
    <motion.li layout={animated} initial={animated ? { opacity: 0, y: 8 } : false} animate={{ opacity: 1, y: 0 }}
      exit={animated ? { opacity: 0, y: 4, transition: { duration: 0.15 } } : { opacity: 0, transition: { duration: 0 } }}
      transition={{ duration: animated ? 0.22 : 0 }}
      className="pointer-events-auto flex w-[340px] items-start gap-3 rounded-md border border-line-strong bg-bg-3 px-3.5 py-3 shadow-[0_12px_32px_rgba(0,0,0,.55)]"
      role="status">
      <Icon className={cn("mt-0.5 size-4 shrink-0", TONE[t.kind])} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="text-[13.5px] font-medium leading-5 text-text-1">{t.title}</div>
        {t.description && <div className="mt-0.5 text-[12.5px] leading-[18px] text-text-2">{t.description}</div>}
      </div>
      <button type="button" onClick={() => dismiss(t.id)} aria-label="Dismiss" className="-mr-1 -mt-0.5 rounded-xs p-1 text-text-3 hover:text-text-1">
        <X className="size-3.5" aria-hidden="true" />
      </button>
    </motion.li>
  );
}

export function Toaster({ animated }: { animated: boolean }) {
  const list = useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => items);
  return (
    <ol aria-label="Notifications" className="pointer-events-none fixed right-4 z-[60] flex flex-col items-end gap-2" style={{ bottom: "var(--toast-bottom, 14px)" }}>
      <AnimatePresence initial={false}>
        {list.map((t) => <ToastCard key={t.id} t={t} animated={animated} />)}
      </AnimatePresence>
    </ol>
  );
}
