// App shell (design-system/pages/shell.md): top bar (brand, breadcrumb, account switcher, engine pill, ET clock,
// Effects, Ctrl K hint, KILL and FLAT ALL), ticker tape, the critical-banner slot, the page area with the dock's bottom
// padding, and the floating dock with its minimize circle (not on Home). Ported from the step-14a mock.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Clock, Leaf, LayoutGrid, Minus, Sparkles, SunMoon } from "lucide-react";
import { cn } from "@/lib/utils";
import { etClock } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { FloatingDock } from "@/components/ui/floating-dock";
import { InfiniteSlider } from "@/components/ui/infinite-slider";
import { Status, StatusIndicator, StatusLabel, type StatusVariant } from "@/components/ui/status";
import { useEffectsState, type EffectsSetting } from "@/effects";
import { LogoMark, Wordmark } from "./brand";
import { PAGES, type PageId } from "./pages";
import type { ServerStatus } from "./status";
import { RiskControls, type ActionResult } from "./RiskControls";
import { NotificationBell } from "./NotificationBell";
import { AccountSwitcher } from "./AccountSwitcher";
import { DATE_BLOCK_MIN_W, DATE_BLOCK_PAD, DateBlock } from "./DateBlock";
import { useViewportWidth } from "@/lib/hooks";

// ── top bar ────────────────────────────────────────────────────────────────────────────────────────────────────────
const ENGINE_PILL: Record<string, { variant: StatusVariant; word: string }> = {
  live: { variant: "ok", word: "ENGINE LIVE" }, paused: { variant: "warn", word: "PAUSED" },
  killed: { variant: "crit", word: "KILLED" }, offline: { variant: "stale", word: "OFFLINE" },
};
const EFFECTS_NEXT: Record<EffectsSetting, EffectsSetting> = { auto: "full", full: "calm", calm: "auto" };

function EffectsButton({ onChange }: { onChange: (s: EffectsSetting) => void }) {
  const fx = useEffectsState();
  const forced = fx.forcedBy !== null && fx.setting !== "calm";
  const label = forced ? "CALM (AUTO)" : fx.setting === "auto" ? `AUTO · ${fx.active.toUpperCase()}` : fx.setting.toUpperCase();
  const why = fx.forcedBy === "reduced-motion" ? "Calm: your system asks for reduced motion" : fx.forcedBy === "live-engine" ? "Calm: the live engine runs on this machine in market hours" : "";
  const Icon = fx.setting === "auto" ? SunMoon : fx.active === "full" ? Sparkles : Leaf;
  return (
    <button type="button" onClick={() => onChange(EFFECTS_NEXT[fx.setting])} data-effects-button
      data-hint={`Effects: ${fx.setting} (click for ${EFFECTS_NEXT[fx.setting]})${why ? `. ${why}` : ""}`} aria-label={`Effects: ${label}. Click to switch to ${EFFECTS_NEXT[fx.setting]}.`}
      className="flex h-7 shrink-0 items-center gap-1.5 rounded-full border border-line-strong px-2 font-mono text-[11px] text-text-2 hover:text-text-1 min-[1100px]:px-2.5">
      <Icon className={cn("size-3.5", fx.active === "full" && "text-gold")} aria-hidden="true" />
      <span className="hidden min-[1100px]:inline">{label}</span>
    </button>
  );
}

export function TopBar({ pageName, status, connected, onEffects, onNavigate, onOpenPath, onResult, killOpen, setKillOpen, onPalette, animated, focus, onFocus }: {
  pageName: string; status: ServerStatus | null; connected: boolean; onEffects: (s: EffectsSetting) => void; onNavigate: (p: PageId) => void;
  onOpenPath: (path: string) => void;
  onResult: (r: ActionResult) => void; killOpen: boolean; setKillOpen: (v: boolean) => void; onPalette: () => void; animated: boolean;
  focus: string | null; onFocus: (name: string) => void;
}) {
  const now = useNow();
  const pill = !connected ? { variant: "crit" as const, word: "NO SERVER" } : ENGINE_PILL[status?.engine.state ?? "offline"];
  return (
    // Nothing in the top bar wraps (owner 2026-11-14). As the width shrinks, the breadcrumb goes first (< 1180 px), then
    // the effects label (< 1100), then the Ctrl K hint (< 1040); account, status, clock, KILL and FLAT ALL always stay.
    <header className="relative z-[30] flex h-[52px] shrink-0 items-center gap-3 overflow-hidden whitespace-nowrap border-b border-line bg-bg-1 px-4 min-[1200px]:px-5" data-topbar>
      <button type="button" onClick={() => onNavigate("home")} className="flex shrink-0 items-center gap-2 pr-1" aria-label="CreamFlow, open Home">
        <LogoMark size={24} /><Wordmark className="hidden text-[23px] min-[1100px]:inline" />
      </button>
      <div className="eyebrow hidden min-w-0 truncate min-[1180px]:block"><span className="text-text-2">CREAMFLOW</span> / {pageName.toUpperCase()}</div>
      <AccountSwitcher accounts={status?.accounts ?? []} summary={status?.accounts_all ?? null} focus={focus} onFocus={onFocus} animated={animated}
        onManage={() => onNavigate("accounts")} onAdd={() => onOpenPath("/accounts/new")} />
      <div className="ml-auto flex shrink-0 items-center gap-2.5">
        <Status variant={pill.variant} data-hint={connected ? status?.engine.note : "The panel lost its server. Kill/flat from this page won't arrive: use the emergency page, nqbot kill / nqbot flat, or Discord."}>
          <StatusIndicator pulse={pill.variant === "ok"} /><StatusLabel>{pill.word}</StatusLabel>
        </Status>
        <span className={cn("flex items-center gap-1 font-mono text-[13px]", connected ? "text-text-1" : "cf-stale")} data-hint={connected ? "Eastern time from this computer's clock" : "No server: the clock is this computer's, not checked"}>
          {!connected && <Clock className="size-3.5" aria-hidden="true" />}{etClock(now)} <span className="text-text-3">ET</span>
        </span>
        <EffectsButton onChange={onEffects} />
        <button type="button" onClick={onPalette} className="hidden h-7 items-center rounded-sm border border-line px-2 font-mono text-[11px] text-text-3 hover:text-text-1 min-[1040px]:flex" aria-label="Open the command palette (Ctrl K)">Ctrl K</button>
        <NotificationBell animated={animated} />
        <RiskControls onResult={onResult} killOpen={killOpen} setKillOpen={setKillOpen} engineHere={status?.engine.here ?? false}
          accounts={(status?.accounts ?? []).filter((x) => !x.draft).length} />
      </div>
    </header>
  );
}

// ── ticker tape ────────────────────────────────────────────────────────────────────────────────────────────────────
export interface TapeItem { name: string; px: string; role?: "floor" | "ceiling"; note?: string }

export function TickerTape({ items, calm, note }: { items: TapeItem[]; calm: boolean; note: string }) {
  if (items.length === 0) {
    // no live levels before the live engine (step 22): an honest static row, nothing scrolls
    return (
      <div className="flex h-7 shrink-0 items-center gap-2 border-b border-line bg-bg-0 px-4 font-mono text-[11px] text-text-3" aria-label="Key levels" data-tape="empty">
        <span className="eyebrow text-text-2">KEY LEVELS</span><span>{note}</span>
      </div>
    );
  }
  const row = items.map((it, i) => (
    <span key={i} className="flex items-center gap-2">
      <span className="text-text-3">{it.name}</span><span className="text-text-1">{it.px}</span>
      {it.role && <span className={cn("rounded-xs px-1 text-[10px]", it.role === "floor" ? "bg-pnl-up/10 text-pnl-up" : "bg-pnl-down/10 text-pnl-down")}>{it.role === "floor" ? "F" : "C"}</span>}
      {it.note && <span className="text-text-3">{it.note}</span>}
    </span>
  ));
  return (
    <div className="h-7 shrink-0 border-b border-line bg-bg-0" aria-label="Key levels" data-tape="levels">
      <InfiniteSlider running={!calm} gap={32} duration={60} durationOnHover={240} className="h-full">
        <div className="flex h-7 items-center gap-8 px-4 font-mono text-[11px] whitespace-nowrap">{row}</div>
      </InfiniteSlider>
    </div>
  );
}

// ── dock ───────────────────────────────────────────────────────────────────────────────────────────────────────────
// The dock's footprint from the bottom edge (bottom offset + height) plus air: every page gets this much bottom padding
// so nothing stays behind the dock when scrolled to the bottom (owner 2026-11-15)
const DOCK_BOTTOM = 16, DOCK_H = 64, DOCK_MIN_D = 44, DOCK_AIR = 16;
// Where floating bars (the unsaved-changes bar) sit (owner 2026-11-15): just above the open dock, clear of its hover
// labels (they rise ~28 px above the dock's top edge), or near the bottom edge while the dock is minimized (the circle
// is in the corner, the date block in the other) or absent
const DOCK_LABEL_ZONE = 28, BAR_GAP = 10;
export const BAR_BOTTOM = { open: DOCK_BOTTOM + DOCK_H + DOCK_LABEL_ZONE + BAR_GAP, low: DOCK_BOTTOM } as const;
export interface DockLayout { /** the bar's bottom offset in px for the current dock state */ barBottom: number }
const DockLayoutContext = createContext<DockLayout>({ barBottom: BAR_BOTTOM.low });
export const useDockLayout = () => useContext(DockLayoutContext);
export const DOCK_PAD = { open: DOCK_BOTTOM + DOCK_H + DOCK_AIR, min: DOCK_BOTTOM + DOCK_MIN_D + DOCK_AIR } as const;
const TOAST_EDGE = 14;
const DOCK_MIN_KEY = "creamflow.dockMinimized";
let dockMinHere: boolean | null = null;
function readDockMin() {
  try { const v = sessionStorage.getItem(DOCK_MIN_KEY); if (v !== null) return v === "1"; } catch { /* storage blocked */ }
  return dockMinHere ?? false;
}
function writeDockMin(v: boolean) {
  dockMinHere = v;
  try { sessionStorage.setItem(DOCK_MIN_KEY, v ? "1" : "0"); } catch { /* storage blocked: this page load still remembers */ }
}

function Dock({ active, animated, minimized, onMinimized, onNavigate }: {
  active: PageId; animated: boolean; minimized: boolean; onMinimized: (v: boolean) => void; onNavigate: (p: PageId) => void;
}) {
  const spring = animated ? { type: "spring" as const, stiffness: 420, damping: 36 } : { duration: 0 };
  const fade = (delay: number) => (animated ? { duration: 0.14, delay } : { duration: 0 });
  return (
    // z 46: above the unsaved-changes bar (45), so the hover labels are never hidden by it (owner 2026-11-15)
    <div className={cn("pointer-events-none fixed left-0 right-0 z-[46] flex px-4", minimized ? "justify-end" : "justify-center")} style={{ bottom: DOCK_BOTTOM }}>
      <motion.div layout transition={spring} data-dock={minimized ? "min" : "open"}
        // dark glass (owner 2026-11-15): 90% #111111 keeps the icons readable over the cream footer dots
        className="pointer-events-auto border border-white/[.08] bg-bg-0/90 shadow-[0_18px_40px_rgba(0,0,0,.5)] backdrop-blur-md"
        style={{ borderRadius: minimized ? DOCK_MIN_D / 2 : 12 }}>
        <AnimatePresence mode="popLayout" initial={false}>
          {minimized ? (
            <motion.button key="min" type="button" aria-label="Show the page dock" aria-expanded={false} onClick={() => onMinimized(false)}
              initial={{ opacity: 0 }} animate={{ opacity: 1, transition: fade(0.12) }} exit={{ opacity: 0, transition: fade(0) }}
              className="grid place-items-center rounded-full text-cream hover:text-gold" style={{ width: DOCK_MIN_D - 2, height: DOCK_MIN_D - 2 }}>
              <LayoutGrid className="size-[18px]" strokeWidth={1.6} aria-hidden="true" />
            </motion.button>
          ) : (
            <motion.div key="open" initial={{ opacity: 0 }} animate={{ opacity: 1, transition: fade(0.12) }} exit={{ opacity: 0, transition: fade(0) }}>
              <FloatingDock animated={animated} active={active} onPick={(id) => onNavigate(id as PageId)}
                className="border-0 bg-transparent shadow-none backdrop-blur-none"
                items={PAGES.map((p) => { const Icon = p.icon; return { id: p.id, title: p.unavailable ? `${p.name} (${p.unavailable})` : p.name, icon: <Icon strokeWidth={1.6} aria-hidden="true" />, disabled: !!p.unavailable }; })}
                trailing={
                  <button type="button" aria-label="Minimize the page dock" aria-expanded={true} data-hint="Minimize the page dock" onClick={() => onMinimized(true)}
                    className="grid size-10 place-items-center rounded-full border border-white/10 bg-bg-1 text-text-3 hover:text-text-1">
                    <Minus className="size-4" strokeWidth={1.8} aria-hidden="true" />
                  </button>
                } />
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}

export function AppFrame({ active, animated, hideDock, onNavigate, top, children }: {
  active: PageId; animated: boolean; hideDock: boolean; onNavigate: (p: PageId) => void; top: ReactNode; children: ReactNode;
}) {
  const [minimized, setMinimized] = useState(readDockMin);
  const onMinimized = (v: boolean) => { writeDockMin(v); setMinimized(v); };
  const showDock = !hideDock;
  const vw = useViewportWidth();
  // every page keeps room for the dock and for the date block in the bottom-left corner (owner 2026-11-15)
  const datePad = vw >= DATE_BLOCK_MIN_W ? DATE_BLOCK_PAD : 0;
  const dockPad = Math.max(showDock ? (minimized ? DOCK_PAD.min : DOCK_PAD.open) : 0, datePad);
  // toasts sit in the bottom-right corner, always above the dock's footprint (owner 2026-11-15)
  const toastBottom = showDock ? Math.max(dockPad, DOCK_PAD.min) : TOAST_EDGE;
  // on the page root: the toasts are drawn outside this frame (a stage-1 bug kept them at the 14-px fallback)
  useEffect(() => { document.documentElement.style.setProperty("--toast-bottom", `${toastBottom}px`); }, [toastBottom]);
  return (
    <DockLayoutContext.Provider value={{ barBottom: showDock && !minimized ? BAR_BOTTOM.open : BAR_BOTTOM.low }}>
    <div className="flex h-full flex-col" style={{ ["--toast-bottom" as string]: `${toastBottom}px` }} data-app-frame>
      {top}
      {/* --dock-pad: a page that ends in the dot-matrix footer cancels it (the page ends inside the letters) */}
      {/* the scrollbar's space is reserved on both sides of every page all the time (scrollbar-gutter: stable both-edges):
          a long page's scrollbar appearing mid-transition never narrows the content (it shifted 7.5 px at 1440 px), and
          every page, Home included, stays exactly centred in the window (owner 2026-11-15) */}
      <main id="cf-main" tabIndex={-1} className="relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden outline-none [scrollbar-gutter:stable_both-edges]"
        style={{ paddingBottom: dockPad, ["--dock-pad" as string]: `${dockPad}px` }}>
        {children}
      </main>
      {showDock && <Dock active={active} animated={animated} minimized={minimized} onMinimized={onMinimized} onNavigate={onNavigate} />}
      <DateBlock />
    </div>
    </DockLayoutContext.Provider>
  );
}
