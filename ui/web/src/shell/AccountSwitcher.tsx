// The top-bar account switcher (owner 2026-11-15; pages/shell.md "account switcher"): the structure of 21st.dev
// Workspaces id 4511 (efferd; code read 2026-11-15: no network calls; its demo's CDN logos not used) — a trigger, a
// titled list with a check on the current one, footer actions — on our own portal instead of Radix Popover and Avatar
// (no new packages). It opens on hover after HOVER_OPEN_MS and stays open while the pointer is over the trigger or the
// list, on click, and from the keyboard (Enter / Space / ArrowDown; arrows, Home / End, Enter picks, Esc closes). One row
// per account: name, type badge and stage; status; room to the floor with a thin bar (amber, then red, as it shrinks);
// today's P&L; progress to the profit target (evaluation) or to the next payout (funded); lives left. Live numbers come
// with the engine (step 22) and show "—" until then. Picking a row sets the panel's focus only (lib/focus.ts).
// Pinning (owner 2026-11-15): hover opens it and moving away closes it; a click on the trigger pins it open (also when
// it is already open from hovering); pinned, it closes only on a second click on the trigger, a click outside, or Esc.
// No row stays highlighted unless the pointer is over it or the arrow keys moved to it.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { Check, ChevronDown, Info, Plus, Settings2, Wallet } from "lucide-react";
import { cn } from "@/lib/utils";
import { useInertBehind } from "@/lib/overlay";
import { money0, money } from "@/lib/format";
import type { AccountBrief } from "./status";

const HOVER_OPEN_MS = 150, HOVER_CLOSE_MS = 220;
const ROW_H = 92;
const VISIBLE_ROWS = 5;

function stage(a: AccountBrief) { return a.phase === "funded" ? "FUNDED" : "EVAL"; }

function Status({ a }: { a: AccountBrief }) {
  const s = a.live.state;
  if (a.draft) return <span className="text-[12px] italic text-text-3">draft — not checked</span>;
  if (!a.active) return <span className="flex items-center gap-1.5 text-[12px] text-text-2"><span className="size-1.5 rounded-full bg-text-3" />inactive</span>;
  if (s === "blown") return <span className="flex items-center gap-1.5 text-[12px] font-semibold text-crit"><span className="size-1.5 rounded-full bg-crit" />blown</span>;
  if (s === "paused") return <span className="flex items-center gap-1.5 text-[12px] text-warn"><span className="size-1.5 rounded-full bg-warn" />paused</span>;
  if (s && s !== "active") return <span className="flex items-center gap-1.5 text-[12px] text-warn"><span className="size-1.5 rounded-full bg-warn" />{s}</span>;
  return <span className="flex items-center gap-1.5 text-[12px] text-text-2"><span className="size-1.5 rounded-full bg-ok" />active</span>;
}

/** room to the floor: the share of the drawdown left; ≥ 50% neutral-healthy, < 50% amber, < 25% red */
function RoomBar({ a }: { a: AccountBrief }) {
  const room = a.live.room_to_floor_usd, dd = a.drawdown_usd;
  const share = room != null && dd ? Math.max(0, Math.min(1, room / dd)) : null;
  const tone = share == null ? "" : share < 0.25 ? "bg-crit" : share < 0.5 ? "bg-warn" : "bg-ok";
  return (
    <div className="flex items-center gap-2">
      <div className={cn("h-1 flex-1 overflow-hidden rounded-full", share == null ? "bg-[repeating-linear-gradient(90deg,rgba(255,255,255,.12)_0_4px,transparent_4px_8px)]" : "bg-white/10")}
        role="meter" aria-label="Room to the floor" aria-valuemin={0} aria-valuemax={dd ?? 0} aria-valuenow={room ?? undefined} aria-valuetext={room == null ? "not known yet" : money0(room)}>
        {share != null && <div className={cn("h-full rounded-full", tone)} style={{ width: `${share * 100}%` }} />}
      </div>
      <span className={cn("w-[104px] shrink-0 text-right font-mono text-[11.5px]", share == null ? "text-text-3" : share < 0.25 ? "text-crit" : share < 0.5 ? "text-warn" : "text-text-1")}>
        {room == null ? "—" : money0(room)} <span className="text-text-3">to floor</span>
      </span>
    </div>
  );
}

function Row({ a, focused, active, onPick, onHover, id }: { a: AccountBrief; focused: boolean; active: boolean; onPick: () => void; onHover: () => void; id: string }) {
  const pnl = a.live.today_pnl_usd;
  const prog = a.live.progress_usd;
  const target = a.target.usd;
  return (
    <div id={id} role="option" aria-selected={focused} onClick={onPick} onMouseMove={onHover} data-switcher-row={a.name} data-lit={active ? "1" : undefined}
      className={cn("grid cursor-pointer grid-cols-[32px_1fr_16px] gap-3 rounded-sm px-3 py-2.5", active ? "bg-cream/[0.08]" : "", a.draft && "opacity-75")}
      style={{ minHeight: ROW_H - 4 }}>
      <span className="mt-0.5 grid size-8 place-items-center rounded-full bg-gold/15 font-mono text-[11px] text-gold" aria-hidden="true">{a.name.slice(0, 2).toUpperCase()}</span>
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate font-mono text-[14px] text-text-1">{a.name}</span>
          <span className="shrink-0 rounded-xs border border-line-strong px-1.5 font-mono text-[10px] leading-4 text-text-2">{a.kind}</span>
          <span className="shrink-0 rounded-xs border border-gold/40 px-1.5 font-mono text-[10px] leading-4 tracking-[0.06em] text-gold">{stage(a)}</span>
          {a.demo && <span className="shrink-0 rounded-xs border border-line-strong px-1.5 font-mono text-[10px] leading-4 text-text-2">DEMO</span>}
          <span className="ml-auto shrink-0"><Status a={a} /></span>
        </div>
        <RoomBar a={a} />
        <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-3 whitespace-nowrap font-mono text-[11.5px] text-text-3">
          <span>today <span className={cn(pnl == null ? "" : pnl > 0 ? "text-pnl-up" : pnl < 0 ? "text-pnl-down" : "text-text-2")}>{pnl == null ? "—" : `${pnl > 0 ? "▲ " : pnl < 0 ? "▼ " : ""}${money(pnl)}`}</span></span>
          <span className="truncate" data-hint={a.target.kind === "payout" ? "Progress to the next payout" : "Progress to the profit target"}>
            {a.target.kind === "payout" ? "payout " : "target "}
            <span className="text-text-2">{prog == null ? "—" : money0(prog)}</span>
            {target != null && <> / {money0(target)}</>}
          </span>
          <span>lives <span className="text-text-2">{a.live.lives_left ?? "—"}</span></span>
        </div>
      </div>
      <span className="mt-1">{focused && <Check className="size-4 text-cream" aria-label="focused" />}</span>
    </div>
  );
}

export function AccountSwitcher({ accounts, focus, onFocus, onManage, onAdd, animated }: {
  accounts: AccountBrief[]; focus: string | null; onFocus: (name: string) => void; onManage: () => void; onAdd: () => void; animated: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);
  useInertBehind(open, "switcher");
  // -1 = nothing highlighted (the pointer isn't over a row and the keys haven't moved)
  const [active, setActive] = useState(-1);
  const [pos, setPos] = useState({ top: 56, left: 16 });
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);
  // archived accounts (failed / closed) aren't offered here; "All accounts" (focus "all") combines every account, archived
  // ones included, on pages that filter by account (owner 2026-11-16)
  const listed = accounts.filter((a) => !a.archived);
  const allFocus = focus === "all";
  const current = allFocus ? null : (listed.find((a) => a.name === focus) ?? listed.find((a) => !a.draft) ?? listed[0]);
  const total = listed.length + (listed.length ? 1 : 0);   // row 0 is "All accounts"

  const clearTimers = () => { window.clearTimeout(openTimer.current); window.clearTimeout(closeTimer.current); };
  const show = useCallback((viaKeys = false, pin = false) => {
    clearTimers();
    setActive(-1);
    setPinned(pin);
    setOpen(true);
    if (viaKeys) requestAnimationFrame(() => panel.current?.focus());
  }, []);
  const hide = useCallback((refocus = false) => { clearTimers(); setOpen(false); setPinned(false); if (refocus) btn.current?.focus(); }, []);
  const hoverIn = () => { window.clearTimeout(closeTimer.current); if (!open) { window.clearTimeout(openTimer.current); openTimer.current = window.setTimeout(() => show(false), HOVER_OPEN_MS); } };
  // a pinned list ignores the pointer leaving
  const hoverOut = () => { window.clearTimeout(openTimer.current); if (!pinned) closeTimer.current = window.setTimeout(() => { setOpen(false); setActive(-1); }, HOVER_CLOSE_MS); };
  // click: closed -> open and pinned; open from hovering -> pinned (stays open); pinned -> closed
  const onTriggerClick = () => { if (!open) show(false, true); else if (!pinned) { clearTimers(); setPinned(true); } else hide(); };
  useEffect(() => () => clearTimers(), []);

  useLayoutEffect(() => {
    if (!open || !btn.current) return;
    const place = () => { const r = btn.current!.getBoundingClientRect(); setPos({ top: r.bottom + 6, left: Math.max(8, Math.min(r.left, window.innerWidth - Math.min(470, window.innerWidth - 16) - 8)) }); };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { const t = e.target as Node; if (!panel.current?.contains(t) && !btn.current?.contains(t)) { setOpen(false); setPinned(false); } };
    // Esc closes it wherever focus is (pinned, focus often stays on the trigger)
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !panel.current?.contains(document.activeElement)) { e.preventDefault(); hide(btn.current === document.activeElement); } };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc); };
  }, [open, hide]);
  useEffect(() => {
    // keep the active row in view while moving with the keys
    const el = panel.current?.querySelector(`[data-row-index="${active}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const pickName = (name: string) => { onFocus(name); hide(true); };
  const nameAt = (i: number) => (i === 0 ? "all" : listed[i - 1]?.name);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); hide(true); return; }
    if (!total) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => (i < 0 ? 0 : Math.min(i + 1, total - 1))); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => (i < 0 ? total - 1 : Math.max(i - 1, 0))); }
    else if (e.key === "Home") { e.preventDefault(); setActive(0); }
    else if (e.key === "End") { e.preventDefault(); setActive(total - 1); }
    else if ((e.key === "Enter" || e.key === " ") && document.activeElement === panel.current) { e.preventDefault(); if (active >= 0) pickName(nameAt(active)); }
  };

  return (
    <>
      <button ref={btn} type="button" aria-haspopup="listbox" aria-expanded={open} data-account-switcher
        onClick={onTriggerClick} onMouseEnter={hoverIn} onMouseLeave={hoverOut} data-pinned={open && pinned ? "1" : undefined}
        onKeyDown={(e) => { if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) { e.preventDefault(); show(true, true); } }}
        aria-label={allFocus ? "Account focus: All accounts. Opens the account list." : current ? `Account focus: ${current.name}. Opens the account list.` : "Accounts"}
        className={cn("flex h-8 shrink-0 items-center gap-2 rounded-sm border bg-bg-2 px-2.5 text-[13px] text-text-1 hover:border-text-3 min-[1180px]:ml-2", open ? "border-text-3" : "border-line-strong")}>
        {allFocus ? (
          <>
            <span className="grid size-5 place-items-center rounded-full bg-gold/15 font-mono text-[9px] text-gold">ALL</span>
            <span className="font-mono">All accounts</span>
            <span className="font-mono text-[11px] text-text-3">{listed.length}</span>
          </>
        ) : current ? (
          <>
            <span className="grid size-5 place-items-center rounded-full bg-gold/15 font-mono text-[10px] text-gold">{current.name.slice(0, 2).toUpperCase()}</span>
            <span className="font-mono">{current.name}</span>
            <span className="rounded-xs border border-gold/40 px-1 font-mono text-[10px] leading-4 tracking-[0.06em] text-gold">{current.draft ? "DRAFT" : stage(current)}</span>
            {listed.length > 1 && <span className="font-mono text-[11px] text-text-3">+{listed.length - 1}</span>}
          </>
        ) : (<><Wallet className="size-4 text-text-2" aria-hidden="true" /><span className="text-text-2">No accounts yet</span></>)}
        <ChevronDown className={cn("size-3.5 text-text-3 transition-transform", open && "rotate-180")} aria-hidden="true" />
      </button>
      {createPortal(
        <AnimatePresence>
          {open && (
            <motion.div ref={panel} tabIndex={-1} role="dialog" aria-label="Accounts" onKeyDown={onKey} data-switcher-panel
              onMouseEnter={() => window.clearTimeout(closeTimer.current)} onMouseLeave={hoverOut}
              initial={animated ? { opacity: 0, y: -4 } : false} animate={{ opacity: 1, y: 0 }} exit={animated ? { opacity: 0, y: -4, transition: { duration: 0.12 } } : { opacity: 0, transition: { duration: 0 } }}
              transition={{ duration: animated ? 0.16 : 0 }}
              className="cf-bare-focus fixed z-[40] w-[min(470px,calc(100vw-16px))] overflow-hidden rounded-lg border border-line-strong bg-bg-3 text-text-1 shadow-[0_12px_32px_rgba(0,0,0,.55)] outline-none"
              style={{ top: pos.top, left: pos.left }}>
              <div className="flex items-center justify-between border-b border-line-strong px-4 py-2.5">
                <span className="text-[13px] font-semibold">Accounts <span className="font-mono text-[12px] font-normal text-text-3">{listed.length}</span></span>
                <span className="font-mono text-[11px] text-text-3">↑↓ move · Enter focus · Esc close</span>
              </div>
              {listed.length === 0 ? (
                <div className="px-4 py-6 text-center text-[13px] text-text-2">No accounts yet.</div>
              ) : (
                <div role="listbox" aria-label="Accounts" aria-activedescendant={active >= 0 ? `sw-${nameAt(active)}` : undefined}
                  onMouseLeave={() => setActive(-1)} className="overflow-y-auto p-1.5" style={{ maxHeight: ROW_H * VISIBLE_ROWS }}>
                  <div data-row-index={0}>
                    <div id="sw-all" role="option" aria-selected={allFocus} onClick={() => pickName("all")} onMouseMove={() => setActive(0)} data-switcher-row="all" data-lit={active === 0 ? "1" : undefined}
                      className={cn("grid cursor-pointer grid-cols-[32px_1fr_16px] items-center gap-3 rounded-sm px-3 py-2.5", active === 0 ? "bg-cream/[0.08]" : "")}>
                      <span className="grid size-8 place-items-center rounded-full bg-gold/15 font-mono text-[10px] text-gold" aria-hidden="true">ALL</span>
                      <div className="min-w-0"><div className="font-mono text-[14px] text-text-1">All accounts</div>
                        <div className="truncate font-mono text-[11.5px] text-text-3">every account's trades together, archived ones included</div></div>
                      <span>{allFocus && <Check className="size-4 text-cream" aria-label="focused" />}</span>
                    </div>
                  </div>
                  {listed.map((a, i) => (
                    <div key={a.name} data-row-index={i + 1}>
                      <Row a={a} id={`sw-${a.name}`} focused={a.name === current?.name} active={i + 1 === active} onPick={() => pickName(a.name)} onHover={() => setActive(i + 1)} />
                    </div>
                  ))}
                </div>
              )}
              <p className="m-0 flex items-start gap-1.5 border-t border-line-strong px-4 py-2 text-[11.5px] leading-4 text-text-3">
                <Info className="mt-px size-3.5 shrink-0" aria-hidden="true" />
                Picking an account sets the panel's focus: pages that filter by account follow it. It never changes which accounts trade.
              </p>
              <div className="flex items-center gap-1 border-t border-line-strong p-1.5">
                <button type="button" onClick={() => { hide(); onManage(); }} className="flex h-8 flex-1 items-center gap-2 rounded-sm px-2.5 text-left text-[13px] text-text-1 hover:bg-bg-2">
                  <Settings2 className="size-4 text-text-2" aria-hidden="true" />Manage accounts
                </button>
                <button type="button" onClick={() => { hide(); onAdd(); }} className="flex h-8 items-center gap-2 rounded-sm px-2.5 text-[13px] text-cream hover:bg-bg-2">
                  <Plus className="size-4" aria-hidden="true" />Add account
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
