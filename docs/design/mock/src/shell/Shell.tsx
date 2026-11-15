// App shell mock (design-system/pages/shell.md): sidebar, top bar, ticker tape. Mock data only.
import { useEffect, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  House, Map, Wallet, SlidersHorizontal, ScanEye, ChartNoAxesCombined, Activity,
  OctagonX, ChevronDown, PanelLeftClose, PanelLeftOpen, Sparkles, Leaf, Minus, LayoutGrid,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { LiquidNavbar } from "@/components/ui/liquid-glass-navbar";
import { FloatingDock } from "@/components/ui/floating-dock";

export const PAGES = [
  { id: "home", name: "Home", icon: House, group: "TRADING" },
  { id: "live", name: "Live", icon: Activity, group: "TRADING", disabled: "from step 21" },
  { id: "gameplan", name: "Gameplan", icon: Map, group: "TRADING" },
  { id: "accounts", name: "Accounts", icon: Wallet, group: "SETUP" },
  { id: "settings", name: "Settings", icon: SlidersHorizontal, group: "SETUP" },
  { id: "review", name: "Review", icon: ScanEye, group: "RESEARCH" },
  { id: "performance", name: "Performance", icon: ChartNoAxesCombined, group: "RESEARCH" },
] as const;
export type PageId = (typeof PAGES)[number]["id"];

export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <defs>
        <linearGradient id="cf-cream-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#FBF1DC" />
          <stop offset="1" stopColor="#E9D3A6" />
        </linearGradient>
      </defs>
      <path d="M46.6 17.4 A21 21 0 1 0 46.6 46.6" fill="none" stroke="url(#cf-cream-g)" strokeWidth="9" strokeLinecap="round" />
      <path d="M24 37 C30 37 31 27 37 27 S44 35 50 31 L56 26" fill="none" stroke="#E7BF72" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="56" cy="26" r="3.2" fill="#FF9A3C" />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("font-display leading-none tracking-[-0.01em] text-cream", className)}>
      Cream<span className="italic text-gold">Flow</span>
    </span>
  );
}

function useEtClock() {
  const fmt = () =>
    new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date());
  const [t, setT] = useState(fmt);
  useEffect(() => { const id = setInterval(() => setT(fmt()), 1000); return () => clearInterval(id); }, []);
  return t;
}

/** Viewport width, for the shell's narrow-screen rules (pages/shell.md). */
export function useViewportWidth() {
  const [w, setW] = useState(() => window.innerWidth);
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return w;
}

export function Sidebar({ active, onNavigate, collapsed = false }: { active: PageId; onNavigate?: (p: PageId) => void; collapsed?: boolean }) {
  const groups = ["TRADING", "SETUP", "RESEARCH"] as const;
  return (
    <aside className={cn("flex shrink-0 flex-col border-r border-line bg-bg-1", collapsed ? "w-16" : "w-[248px]")}>
      <div className={cn("flex h-[52px] items-center gap-2.5 border-b border-line", collapsed ? "justify-center" : "px-4")}>
        <LogoMark size={26} />
        {!collapsed && <Wordmark className="text-[25px]" />}
        {!collapsed && <PanelLeftClose className="ml-auto size-4 text-text-3" aria-hidden="true" />}
      </div>
      <nav className={cn("flex-1 overflow-y-auto py-3", collapsed ? "px-2" : "px-3")} aria-label="Pages">
        {groups.map((g) => (
          <div key={g} className="mb-3">
            {collapsed
              ? <div className="mx-2 mb-2 mt-1 h-px bg-line" aria-hidden="true" />
              : <div className="eyebrow flex items-center gap-1 px-2 py-1.5"><ChevronDown className="size-3" aria-hidden="true" />{g}</div>}
            {PAGES.filter((p) => p.group === g).map((p) => {
              const Icon = p.icon;
              const isActive = p.id === active;
              const disabled = "disabled" in p;
              return (
                <button
                  key={p.id}
                  disabled={disabled}
                  onClick={() => onNavigate?.(p.id)}
                  title={collapsed ? p.name : undefined}
                  aria-label={collapsed ? p.name : undefined}
                  className={cn(
                    "relative flex h-9 w-full items-center gap-3 rounded-sm text-left text-[13.5px] transition-colors duration-150",
                    collapsed ? "justify-center" : "px-2.5",
                    isActive ? "text-cream" : "text-text-2 hover:bg-bg-2 hover:text-text-1",
                    disabled && "cursor-not-allowed opacity-45 hover:bg-transparent hover:text-text-2",
                  )}
                >
                  {isActive && (
                    <motion.span layoutId="nav-pill" className="absolute inset-0 rounded-sm border border-gold/25 bg-gold/10" transition={{ type: "spring", stiffness: 420, damping: 34 }} />
                  )}
                  <Icon className="relative size-[18px]" strokeWidth={1.5} aria-hidden="true" />
                  {!collapsed && <span className="relative">{p.name}</span>}
                  {disabled && !collapsed && <span className="relative ml-auto font-mono text-[10px] text-text-3">{(p as { disabled: string }).disabled}</span>}
                </button>
              );
            })}
          </div>
        ))}
      </nav>
      {collapsed ? (
        <div className="flex flex-col items-center gap-2 border-t border-line py-3">
          <span className="font-display text-[26px] leading-none text-text-1">14</span>
          <span className="size-1.5 rounded-full bg-text-3" title="Engine offline" role="img" aria-label="Engine offline" />
          <PanelLeftOpen className="size-4 text-text-3" aria-hidden="true" />
        </div>
      ) : (
        <div className="border-t border-line px-4 py-3">
          <div className="flex items-baseline gap-2">
            <span className="font-display text-[30px] leading-none text-text-1">14</span>
            <div className="eyebrow leading-tight">Nov 2026<br />Fri · Eastern</div>
          </div>
          <div className="mt-2 flex items-center gap-2 font-mono text-[11px] text-text-2">
            <span className="size-1.5 rounded-full bg-text-3" aria-hidden="true" /> ENGINE OFFLINE · build PC
          </div>
        </div>
      )}
    </aside>
  );
}

export function TopBar({ page, effects, onEffects, showBrand = false }: { page: string; effects: "full" | "calm"; onEffects?: () => void; showBrand?: boolean }) {
  const clock = useEtClock();
  return (
    // Nothing in the top bar wraps (owner 2026-11-14). As the width shrinks, the breadcrumb goes first, then the
    // Ctrl K hint, then the effects label (its icon stays); account, status, clock and FLAT ALL always stay.
    <header className="flex h-[52px] shrink-0 items-center gap-3 overflow-hidden whitespace-nowrap border-b border-line bg-bg-1 px-4 min-[1200px]:px-5">
      {showBrand && <span className="flex shrink-0 items-center gap-2 pr-1"><LogoMark size={24} /><Wordmark className="hidden text-[23px] min-[1100px]:inline" /></span>}
      <div className="eyebrow hidden min-w-0 truncate min-[1180px]:block"><span className="text-text-2">CREAMFLOW</span> / {page.toUpperCase()}</div>
      <button className="flex h-8 shrink-0 items-center gap-2 rounded-sm border border-line-strong bg-bg-2 px-2.5 text-[13px] text-text-1 hover:border-text-3 min-[1180px]:ml-2">
        <span className="grid size-5 place-items-center rounded-full bg-gold/15 font-mono text-[10px] text-gold">M1</span>
        mffur1
        <span className="rounded-xs border border-gold/40 px-1 font-mono text-[10px] leading-4 tracking-[0.06em] text-gold">EVAL</span>
        <ChevronDown className="size-3.5 text-text-3" aria-hidden="true" />
      </button>
      <div className="ml-auto flex shrink-0 items-center gap-2.5">
        <span className="flex h-7 items-center gap-2 rounded-full border border-line-strong px-3 font-mono text-[11px] tracking-[0.08em] text-text-2">
          <span className="size-1.5 rounded-full bg-text-3" aria-hidden="true" /> OFFLINE
        </span>
        <span className="font-mono text-[13px] text-text-1">{clock} <span className="text-text-3">ET</span></span>
        <button onClick={onEffects} title={effects === "full" ? "Effects: Full" : "Effects: Calm"} aria-label={effects === "full" ? "Effects: Full" : "Effects: Calm"}
          className="flex h-7 items-center gap-1.5 rounded-full border border-line-strong px-2 font-mono text-[11px] text-text-2 hover:text-text-1 min-[1100px]:px-2.5">
          {effects === "full" ? <Sparkles className="size-3.5 text-gold" aria-hidden="true" /> : <Leaf className="size-3.5" aria-hidden="true" />}
          <span className="hidden min-[1100px]:inline">{effects === "full" ? "FULL" : "CALM"}</span>
        </button>
        <span className="hidden h-7 items-center rounded-sm border border-line px-2 font-mono text-[11px] text-text-3 min-[1040px]:flex">Ctrl K</span>
        <button className="flex h-9 shrink-0 items-center gap-2 whitespace-nowrap rounded-sm bg-crit-fill px-4 text-[13px] font-semibold tracking-[0.04em] text-white shadow-[0_0_0_1px_rgba(255,255,255,.08)_inset] hover:bg-[#e02a31]">
          <OctagonX className="size-4" strokeWidth={2} aria-hidden="true" /> FLAT ALL
        </button>
      </div>
    </header>
  );
}

const TAPE = [
  ["QQQ KING", "21,450.00", "floor", "held ×2"], ["VWAP", "21,431.75", "", "+1σ 21,468.25"], ["PDH", "21,512.00", "ceiling", "armed"],
  ["ON LOW", "21,366.50", "floor", "armed"], ["IB HIGH", "21,478.25", "ceiling", "08:45 lock"], ["GOLDEN ZONE", "21,388.50–21,401.75", "floor", "deep"],
  ["NDXP King", "21,500.00", "ceiling", "held ×1"], ["POC", "21,442.00", "", "naked prev"], ["VAL", "21,401.00", "floor", "armed"],
] as const;

export function TickerTape({ calm }: { calm: boolean }) {
  const items = [...TAPE, ...TAPE];
  return (
    <div className="relative h-7 shrink-0 overflow-hidden border-b border-line bg-bg-0" aria-label="Key levels">
      <div className={cn("flex h-full w-max items-center gap-8 px-4 font-mono text-[11px] whitespace-nowrap", !calm && "animate-[tape_60s_linear_infinite] hover:[animation-play-state:paused]")}>
        {items.map(([name, px, role, note], i) => (
          <span key={i} className="flex items-center gap-2">
            <span className="text-text-3">{name}</span>
            <span className="text-text-1">{px}</span>
            {role && <span className={cn("rounded-xs px-1 text-[10px]", role === "floor" ? "bg-pnl-up/10 text-pnl-up" : "bg-pnl-down/10 text-pnl-down")}>{role === "floor" ? "F" : "C"}</span>}
            <span className="text-text-3">{note}</span>
          </span>
        ))}
      </div>
      <style>{`@keyframes tape { from { transform: translateX(0) } to { transform: translateX(-50%) } }`}</style>
    </div>
  );
}

/** The three animated navigation options for the owner's pick (step 14a, 2026-11-15): `sidebar` = the sidebar with
 *  its sliding pill indicator; `waterline` = the owner's Liquid Navbar (21st.dev 43059) as a floating page switcher at
 *  the top of the content; `dock` = a floating dock with magnification (21st.dev 18014) at the bottom. */
export type NavOption = "sidebar" | "waterline" | "dock";

const NAV_ITEMS = PAGES.filter((p) => p.id !== "live");

/** Where the minimized dock's circle sits: `corner` = it moves to the bottom-right corner (the owner's pick 2026-11-15),
 *  `centre` = it stays at the bottom centre (the other variant, kept switchable in the preview). */
export type DockMin = "corner" | "centre";

// The dock's footprint from the bottom edge (bottom offset + height) plus air above it: every page gets this much bottom
// padding, so nothing stays behind the dock when scrolled to the bottom (owner 2026-11-15)
const DOCK_BOTTOM = 16, DOCK_H = 64, DOCK_MIN_D = 44, DOCK_AIR = 16;
export const DOCK_PAD = { open: DOCK_BOTTOM + DOCK_H + DOCK_AIR, min: DOCK_BOTTOM + DOCK_MIN_D + DOCK_AIR } as const;
const TOAST_EDGE = 14; // no dock: toasts 14 px from the bottom edge (Home: below its bottom-right node)

// minimized or not is remembered for the session (sessionStorage; a reload in the same tab keeps it)
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

function Dock({ active, animated, variant, minimized, onMinimized, onNavigate }: {
  active: PageId; animated: boolean; variant: DockMin; minimized: boolean; onMinimized: (v: boolean) => void; onNavigate?: (p: PageId) => void;
}) {
  const spring = animated ? { type: "spring" as const, stiffness: 420, damping: 36 } : { duration: 0 };
  const fade = (delay: number) => (animated ? { duration: 0.14, delay } : { duration: 0 });
  return (
    <div className={cn("pointer-events-none fixed left-0 right-0 z-30 flex px-4", minimized && variant === "corner" ? "justify-end" : "justify-center")}
      style={{ bottom: DOCK_BOTTOM }}>
      {/* one glass shell that morphs between the bar and the circle (layout animation: size and place) */}
      <motion.div layout transition={spring} data-dock={minimized ? "min" : "open"}
        // dark glass (owner 2026-11-15): 90% #111111 keeps the icons readable over the cream footer dots (worst case,
        // pure cream behind: background ≈ #292623; icons sit on opaque bg-3 circles, the minimized circle's cream icon
        // ≈ 12:1)
        className="pointer-events-auto border border-white/[.08] bg-bg-0/90 shadow-[0_18px_40px_rgba(0,0,0,.5)] backdrop-blur-md"
        style={{ borderRadius: minimized ? DOCK_MIN_D / 2 : 12 }}>
        <AnimatePresence mode="popLayout" initial={false}>
          {minimized ? (
            <motion.button key="min" type="button" aria-label="Show the page dock" aria-expanded={false}
              onClick={() => onMinimized(false)}
              initial={{ opacity: 0 }} animate={{ opacity: 1, transition: fade(0.12) }} exit={{ opacity: 0, transition: fade(0) }}
              className="grid place-items-center rounded-full text-cream hover:text-gold focus-visible:outline-2 focus-visible:outline-cream"
              style={{ width: DOCK_MIN_D - 2, height: DOCK_MIN_D - 2 }}>
              <LayoutGrid className="size-[18px]" strokeWidth={1.6} aria-hidden="true" />
            </motion.button>
          ) : (
            <motion.div key="open" initial={{ opacity: 0 }} animate={{ opacity: 1, transition: fade(0.12) }} exit={{ opacity: 0, transition: fade(0) }}>
              <FloatingDock animated={animated} active={active} onPick={(id) => onNavigate?.(id as PageId)}
                className="border-0 bg-transparent shadow-none backdrop-blur-none"
                items={PAGES.map((p) => { const Icon = p.icon; return { id: p.id, title: "disabled" in p ? `${p.name} (from step 21)` : p.name, icon: <Icon strokeWidth={1.6} aria-hidden="true" />, disabled: "disabled" in p }; })}
                trailing={
                  <button type="button" aria-label="Minimize the page dock" aria-expanded={true} title="Minimize"
                    onClick={() => onMinimized(true)}
                    className="grid size-10 place-items-center rounded-full border border-white/10 bg-bg-1 text-text-3 hover:text-text-1 focus-visible:outline-2 focus-visible:outline-cream">
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

export function AppFrame({ page, active, effects, onEffects, nav = "sidebar", onNavigate, hideDock = false, dockMin = "corner", children }: {
  page: string; active: PageId; effects: "full" | "calm"; onEffects?: () => void; nav?: NavOption; onNavigate?: (p: PageId) => void;
  /** Home has no dock: its six nodes, keys 1–6 and Ctrl K cover navigation there (owner 2026-11-15) */
  hideDock?: boolean; dockMin?: DockMin; children: ReactNode;
}) {
  const vw = useViewportWidth();
  const animated = effects === "full";
  const activeIdx = Math.max(0, NAV_ITEMS.findIndex((p) => p.id === active));
  const [minimized, setMinimized] = useState(readDockMin);
  const onMinimized = (v: boolean) => { writeDockMin(v); setMinimized(v); };
  const showDock = nav === "dock" && !hideDock;
  const dockPad = showDock ? (minimized ? DOCK_PAD.min : DOCK_PAD.open) : 0;
  // toasts sit in the bottom-right corner, always above the dock's footprint (open or minimized, either variant;
  // owner 2026-11-15); with no dock (Home) TOAST_EDGE from the edge
  const toastBottom = showDock ? dockPad : TOAST_EDGE;
  return (
    <div className="flex h-full" style={{ ["--toast-bottom" as string]: `${toastBottom}px` }}>
      {nav === "sidebar" && <Sidebar active={active} collapsed={vw < 1100} onNavigate={onNavigate} />}
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar page={page} effects={effects} onEffects={onEffects} showBrand={nav !== "sidebar"} />
        <TickerTape calm={effects === "calm"} />
        {/* --dock-pad: a page that ends in the dot-matrix footer cancels it (the page ends inside the letters) */}
        <main className={cn("relative min-h-0 flex-1 overflow-y-auto", nav === "waterline" && "pt-[70px]")}
          style={{ paddingBottom: dockPad, ["--dock-pad" as string]: `${dockPad}px` }}>
          {nav === "waterline" && (
            <div className="pointer-events-none fixed left-0 right-0 z-30 flex justify-center" style={{ top: 92 }}>
              <div className="pointer-events-auto">
                <LiquidNavbar
                  tone="dark" sound={false} compact={vw < 1280} active={activeIdx}
                  onChange={(i) => onNavigate?.(NAV_ITEMS[i].id)}
                  items={NAV_ITEMS.map((p) => { const Icon = p.icon; return { label: p.name, icon: <Icon className="size-[18px]" strokeWidth={1.6} aria-hidden="true" />, fx: animated ? "pop" : "none" }; })}
                />
              </div>
            </div>
          )}
          {children}
        </main>
        {showDock && <Dock active={active} animated={animated} variant={dockMin} minimized={minimized} onMinimized={onMinimized} onNavigate={onNavigate} />}
      </div>
    </div>
  );
}
