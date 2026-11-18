// Unsaved changes bar: 21st.dev Unsaved changes id 9576 (stvenchg), code read 2026-11-15: no network calls; its
// shadcn Button and class-variance-authority replaced by our own classes (no new packages). CreamFlow changes
// (pages/accounts.md, settings.md; owner 2026-11-15):
// - its place follows the dock: just above the open dock and clear of the dock's hover labels, near the bottom edge
//   while the dock is minimized; it slides between the two (220 ms, ease-out), instantly in Calm;
// - the count of changed fields, the reason when saving is blocked, a note (changes apply between trades, §9b);
// - buttons: Reset (undo every change, stay), Discard (undo every change and leave the editor; shown when the page
//   gives `onDiscard`), and Save (or the page's own label, e.g. Add account).
import { useEffect } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CircleCheck, CircleAlert, LoaderCircle, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { BAR_BOTTOM, useDockLayout } from "@/shell/Shell";

const SLIDE = { duration: 0.22, ease: [0, 0, 0.2, 1] as const };
const BAR_HEIGHT = 44;

export function UnsavedChanges({ open, count, saving, error, blocked, onSave, onReset, onDiscard, saveLabel = "Save", animated = true, note, idleLabel, resetLabel }: {
  open: boolean; count: number; saving?: boolean; error?: string | null; blocked?: string | null; onSave: () => void;
  /** undo every change and stay */
  onReset: () => void;
  /** undo every change and leave the editor (a new account: create nothing) */
  onDiscard?: () => void;
  saveLabel?: string; animated?: boolean; note?: string | null;
  /** shown instead of "0 unsaved changes" when the bar is open with nothing changed (a draft to save as checked) */
  idleLabel?: string;
  /** the Reset button's label where there is no separate Discard (Settings: "Discard") */
  resetLabel?: string;
}) {
  const { barBottom } = useDockLayout();
  const label = error ? error : saving ? "Saving…" : blocked ? blocked : count === 0 && idleLabel ? idleLabel : `${count} unsaved change${count === 1 ? "" : "s"}`;
  const Icon = error ? CircleAlert : saving ? LoaderCircle : blocked ? TriangleAlert : count ? TriangleAlert : CircleCheck;
  const lift = -(barBottom - BAR_BOTTOM.low);
  // while the bar is open, toasts keep clear of it (--cf-bar-clear: the bar's top edge + air; toast.tsx)
  useEffect(() => {
    const root = document.documentElement.style;
    if (open) root.setProperty("--cf-bar-clear", `${barBottom + BAR_HEIGHT + 12}px`); else root.removeProperty("--cf-bar-clear");
    return () => { root.removeProperty("--cf-bar-clear"); };
  }, [open, barBottom]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div role="status" aria-live="polite" data-unsaved data-bar-bottom={barBottom}
          initial={animated ? { opacity: 0, y: lift + 16, scale: 0.97 } : { opacity: 1, y: lift, scale: 1 }}
          animate={{ opacity: 1, y: lift, scale: 1 }}
          exit={animated ? { opacity: 0, y: lift + 16, scale: 0.97 } : { opacity: 0, transition: { duration: 0 } }}
          transition={animated ? { y: SLIDE, opacity: { duration: 0.18 }, scale: { duration: 0.18 } } : { duration: 0 }}
          className="fixed inset-x-0 z-[45] mx-auto flex w-fit max-w-[calc(100vw-32px)] items-center gap-3 rounded-full border border-line-strong bg-bg-3 py-1.5 pl-4 pr-1.5 text-[13px] shadow-[0_12px_32px_rgba(0,0,0,.55)]"
          style={{ bottom: BAR_BOTTOM.low }}>
          <Icon className={cn("size-4 shrink-0", error || blocked ? "text-crit" : saving ? "animate-spin text-text-2" : count ? "text-warn" : "text-text-2")} aria-hidden="true" />
          {/* the dot is its own element (owner 2026-11-18) in a group with its own gap, so the space is the same on both
              sides of it, about as wide as around the app's other separators */}
          <span className="flex items-center gap-1.5">
            <span className="whitespace-nowrap text-text-1">{label}</span>
            {note && <span className="hidden text-text-3 min-[1200px]:inline" aria-hidden="true" data-sep>·</span>}
            {note && <span className="hidden whitespace-nowrap text-text-3 min-[1200px]:inline">{note}</span>}
          </span>
          <span className="ml-2 flex items-center gap-1.5">
            <button type="button" onClick={onReset} disabled={saving || count === 0} data-reset
              data-hint="Undo every unsaved change and stay here"
              className="h-8 rounded-full border border-line-strong bg-bg-2 px-3.5 text-[13px] text-text-1 hover:border-text-3 disabled:cursor-not-allowed disabled:opacity-45">{resetLabel ?? "Reset"}</button>
            {onDiscard && (
              <button type="button" onClick={onDiscard} disabled={saving} data-discard
                data-hint="Undo every unsaved change and close the editor"
                className="h-8 rounded-full border border-line-strong bg-bg-2 px-3.5 text-[13px] text-text-1 hover:border-text-3 disabled:opacity-45">Discard</button>
            )}
            <button type="button" onClick={onSave} disabled={saving || !!blocked} data-save
              className="h-8 rounded-full bg-cream px-4 text-[13px] font-semibold text-[#081a1b] hover:bg-[#fff0e0] disabled:cursor-not-allowed disabled:opacity-45">{saveLabel}</button>
          </span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
