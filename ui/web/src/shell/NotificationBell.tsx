// The bell in the top bar and the notification history (owner 2026-11-15): every banner and toast the panel showed in
// this browser tab, newest first, after it has gone. Built on 21st.dev Notification Panel id 27135 (uvain; code read
// 2026-11-15: React + framer-motion + lucide only, no network calls; its demo's inline base64 portraits are not used,
// and the strict CSP would block them). CreamFlow changes: rows are panel messages (kind icon, title, text, ET time,
// source) instead of people; tabs All / Alerts (warning + critical) / Info instead of Inbox / Following / Archived;
// row menus, actions and archiving dropped (a history, not an inbox); Cocoa level-3 surface; it opens in a portal under
// the bell and below the banner stack (the top bar clips overflow; a banner is never covered), closes on Escape or a
// click outside; the unread badge is crit-red while an
// unread critical message exists, cream otherwise; motion follows Effects.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { Bell, CheckCheck, Info, OctagonAlert, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import { etClock } from "@/lib/format";
import { markAllRead, markRead, useNotes, type Note, type NoteKind } from "@/lib/notify";

type TabId = "all" | "alerts" | "info";
const TABS: { id: TabId; label: string }[] = [{ id: "all", label: "All" }, { id: "alerts", label: "Alerts" }, { id: "info", label: "Info" }];
const ICON: Record<NoteKind, typeof Info> = { info: Info, warn: TriangleAlert, crit: OctagonAlert };
const TINT: Record<NoteKind, string> = { info: "bg-info/15 text-info", warn: "bg-warn/15 text-warn", crit: "bg-crit/15 text-crit" };
const SOURCE: Record<string, string> = { action: "your action", server: "panel server", panel: "panel" };

function dayLabel(iso: string) {
  const d = new Date(iso);
  const f = new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", weekday: "short", day: "numeric", month: "short" });
  return f.format(d).replace(",", "");
}

function Row({ n }: { n: Note }) {
  const Icon = ICON[n.kind];
  return (
    <div className={cn("flex gap-3 px-4 py-3", !n.read && "bg-cream/[0.04]")} onMouseEnter={() => !n.read && markRead(n.id)}>
      <span className={cn("grid size-8 shrink-0 place-items-center rounded-full", TINT[n.kind])} aria-hidden="true"><Icon className="size-4" /></span>
      <div className="min-w-0 flex-1">
        <p className="m-0 text-[13px] leading-[1.45] text-text-2">
          <span className="font-medium text-text-1">{n.title}</span>{n.text ? <> {n.text}</> : null}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-x-1.5 font-mono text-[11px] text-text-3">
          <span>{dayLabel(n.at)} · {etClock(new Date(n.at))} ET</span>
          <span aria-hidden>·</span><span>{SOURCE[n.source] ?? n.source}</span>
          {!n.banner && <><span aria-hidden>·</span><span>toast</span></>}
        </div>
      </div>
      {!n.read ? <span aria-label="Unread" className="mt-[7px] size-[7px] shrink-0 rounded-full bg-cream" /> : <span className="size-[7px] shrink-0" />}
    </div>
  );
}

export function NotificationBell({ animated }: { animated: boolean }) {
  const notes = useNotes();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TabId>("all");
  const [pos, setPos] = useState({ top: 56, right: 16 });
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const unread = notes.filter((n) => !n.read);
  const critUnread = unread.some((n) => n.kind === "crit");
  const shown = notes.slice().reverse().filter((n) => tab === "all" || (tab === "alerts" ? n.kind !== "info" : n.kind === "info"));

  useLayoutEffect(() => {
    if (!open || !btn.current) return;
    // below the banner stack, never over it: a critical banner and its Acknowledge button stay fully visible
    const place = () => {
      const r = btn.current!.getBoundingClientRect();
      const banners = document.querySelector("[data-banners]")?.getBoundingClientRect();
      setPos({ top: Math.max(r.bottom, banners?.bottom ?? 0) + 8, right: Math.max(8, window.innerWidth - r.right - 8) });
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open, notes]);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { const t = e.target as Node; if (!panel.current?.contains(t) && !btn.current?.contains(t)) setOpen(false); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); btn.current?.focus(); } };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", key); };
  }, [open]);

  return (
    <>
      <button ref={btn} type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="dialog" data-bell
        aria-label={`Notifications${unread.length ? `, ${unread.length} unread` : ""}`} data-hint="Notifications (every message shown in this tab)"
        className={cn("relative grid size-8 shrink-0 place-items-center rounded-full border border-line-strong text-text-2 hover:text-text-1", open && "border-text-3 text-text-1")}>
        <Bell className="size-4" strokeWidth={1.6} aria-hidden="true" />
        {unread.length > 0 && (
          <span className={cn("absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full px-1 font-mono text-[10px] font-semibold leading-none",
            critUnread ? "bg-crit-fill text-white" : "bg-cream text-[#081a1b]")} aria-hidden="true">
            {unread.length > 99 ? "99+" : unread.length}
          </span>
        )}
      </button>
      {createPortal(
        <AnimatePresence>
          {open && (
            <motion.div ref={panel} role="dialog" aria-label="Notifications" data-bell-panel
              initial={animated ? { opacity: 0, y: -6, scale: 0.98 } : false} animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={animated ? { opacity: 0, y: -4, transition: { duration: 0.14 } } : { opacity: 0, transition: { duration: 0 } }}
              transition={animated ? { type: "spring", stiffness: 420, damping: 34 } : { duration: 0 }}
              className="fixed z-[40] w-[min(420px,calc(100vw-16px))] origin-top-right overflow-hidden rounded-lg border border-line-strong bg-bg-3 text-text-1 shadow-[0_12px_32px_rgba(0,0,0,.55)]"
              style={{ top: pos.top, right: pos.right }}>
              <div className="flex items-center justify-between gap-3 px-4 pb-0.5 pt-3.5">
                <h2 className="m-0 text-[15px] font-semibold">Notifications</h2>
                <button type="button" onClick={markAllRead} disabled={unread.length === 0}
                  className="inline-flex items-center gap-1.5 rounded-sm px-1.5 py-1 text-[12.5px] font-medium text-text-2 hover:text-text-1 disabled:pointer-events-none disabled:opacity-40">
                  <CheckCheck className="size-3.5" aria-hidden="true" /> Mark all as read
                </button>
              </div>
              <div className="flex items-center gap-0.5 border-b border-line-strong px-3" role="tablist">
                {TABS.map((t) => {
                  const active = t.id === tab;
                  const count = t.id === "alerts" ? unread.filter((n) => n.kind !== "info").length : t.id === "info" ? unread.filter((n) => n.kind === "info").length : 0;
                  return (
                    <button key={t.id} type="button" role="tab" aria-selected={active} onClick={() => setTab(t.id)}
                      className={cn("relative px-2 py-2.5 text-[13px] font-medium", active ? "text-text-1" : "text-text-2 hover:text-text-1")}>
                      <span className="inline-flex items-center gap-1.5">{t.label}
                        {count > 0 && <span className="rounded-full bg-bg-1 px-1.5 py-px font-mono text-[10.5px] text-text-1">{count}</span>}
                      </span>
                      {active && <motion.span layoutId="cf-bell-tab" transition={animated ? { type: "spring", stiffness: 420, damping: 34 } : { duration: 0 }}
                        className="absolute inset-x-1.5 -bottom-px h-[2px] rounded-full bg-cream" />}
                    </button>
                  );
                })}
              </div>
              <div className="max-h-[min(440px,60vh)] divide-y divide-line overflow-y-auto">
                {shown.map((n) => <Row key={n.id} n={n} />)}
                {shown.length === 0 && (
                  <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
                    <span className="grid size-10 place-items-center rounded-md bg-bg-2 text-text-3"><Bell className="size-[18px]" aria-hidden="true" /></span>
                    <p className="m-0 text-[13px] font-medium text-text-1">Nothing yet</p>
                    <p className="m-0 text-[12.5px] text-text-2">Every banner and toast shown in this tab lands here.</p>
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
