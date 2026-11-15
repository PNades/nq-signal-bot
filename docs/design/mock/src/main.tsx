import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, motion } from "framer-motion";
import "./styles.css";
import { AppFrame, PAGES, type DockMin, type NavOption, type PageId } from "./shell/Shell";
import { HomeScene } from "./home/HomeScene";
import { PerformancePage } from "./perf/PerformancePage";
import { StyleTile } from "./tile/StyleTile";
import { THEMES, applyTheme, type ThemeId } from "./themes";
import { EffectsContext } from "./effects";

// Design workspace router: ?view=home|performance|tile [&nav=dock|sidebar|waterline] [&dockmin=corner|centre] [&account=all|mffur1|lucid1] [&tab=equity|drawdown|montecarlo|calendar|trades]
//                          [&calm=1] [&intro=1] [&theme=current|original|thuray|amber]
// &intro=1 replays Home's opening sequence on every open (design only; normally it plays once a session)
const q = new URLSearchParams(location.search);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const themeParam = q.get("theme") as ThemeId | null;
const theme: ThemeId = themeParam && themeParam in THEMES ? themeParam : "current";
applyTheme(theme);
const navParam = q.get("nav");
// the floating dock is the owner's pick (2026-11-15); &nav=sidebar / &nav=waterline still show the other two
const NAV: NavOption = navParam === "waterline" || navParam === "sidebar" ? navParam : "dock";
// the minimized dock goes to the bottom-right corner (owner 2026-11-15); &dockmin=centre shows the other variant
const DOCK_MIN: DockMin = q.get("dockmin") === "centre" ? "centre" : "corner";
type View = PageId | "tile";
const VIEW0 = (q.get("view") ?? "home") as View;

function ThemeSwitcher() {
  const link = (id: ThemeId) => { const p = new URLSearchParams(location.search); p.set("theme", id); return `?${p.toString()}`; };
  return (
    <div className="fixed bottom-2 right-2 z-50 max-w-[560px] rounded-md border border-line-strong bg-bg-3/95 px-2.5 py-2 text-[12px] shadow-[0_12px_32px_rgba(0,0,0,.55)]">
      <div className="flex items-center gap-1.5 whitespace-nowrap">
        <span className="eyebrow mr-1">Theme</span>
        {(Object.keys(THEMES) as ThemeId[]).map((id) => (
          <a key={id} href={link(id)} className={`rounded-sm border px-2 py-0.5 ${id === theme ? "border-gold text-gold" : "border-line text-text-2 hover:text-text-1"}`}>{THEMES[id].name}</a>
        ))}
      </div>
      <div className="mt-1 truncate text-[11px] leading-4 text-text-3" title={THEMES[theme].note}>{THEMES[theme].note}</div>
    </div>
  );
}

function Placeholder({ name }: { name: string }) {
  return (
    <div className="grid h-full place-items-center p-10 text-center">
      <div><div className="eyebrow">CREAMFLOW / {name.toUpperCase()}</div><div className="mt-2 font-display text-[34px]">{name} <span className="italic text-cream">comes with the build</span></div>
        <p className="mt-2 text-[14px] text-text-2">The step-4 mock covers Home, Performance and the style tile.</p></div>
    </div>
  );
}

function App() {
  const [effects, setEffects] = useState<"full" | "calm">(q.get("calm") === "1" || reduced ? "calm" : "full");
  const [view, setView] = useState<View>(VIEW0);
  const toggle = () => setEffects((e) => (e === "full" ? "calm" : "full"));
  const animated = effects === "full";
  const pageName = view === "tile" ? "Style tile" : PAGES.find((p) => p.id === view)?.name ?? "Home";
  const active: PageId = view === "tile" ? "home" : view;
  // the first page shown appears at once (Home plays its own intro); later page changes fade (Home: a 200-ms fade of
  // the formed scene). The wrapper's own `initial={false}` is set per page, never on AnimatePresence: AnimatePresence's
  // `initial={false}` also blocks the entrance animations of everything mounted later inside the first page (it broke
  // Home's node entrance)
  const first = useRef(true);
  useEffect(() => { first.current = false; }, []);
  document.title = `CreamFlow · ${pageName}`;
  const body =
    view === "home" ? <HomeScene key={`home-${effects}`} concept="flow" calm={!animated} forceIntro={q.get("intro") === "1"}
      // the mock opens the pages it has (Performance); the others show "Opening …" until they are built
      onOpenPage={(id) => (id === "performance" ? (setView("performance"), true) : false)} />
      : view === "performance" ? <PerformancePage initialTab={q.get("tab") ?? "equity"} initialAccount={q.get("account") ?? "all"} />
        : view === "tile" ? <StyleTile />
          : <Placeholder name={pageName} />;
  return (
    <EffectsContext.Provider value={effects}>
      <AppFrame page={pageName} active={active} effects={effects} onEffects={toggle} nav={NAV} onNavigate={(p) => setView(p)} hideDock={view === "home"} dockMin={DOCK_MIN}>
        <AnimatePresence mode="wait">
          <motion.div key={view} className={view === "home" ? "absolute inset-0" : "min-h-full"}
            initial={first.current || !animated ? false : view === "home" ? { opacity: 0 } : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }} exit={animated ? { opacity: 0, transition: { duration: 0.15 } } : { opacity: 0, transition: { duration: 0 } }}
            transition={{ duration: animated ? (view === "home" ? 0.2 : 0.32) : 0, ease: [0.2, 0, 0, 1] }}>
            {body}
          </motion.div>
        </AnimatePresence>
      </AppFrame>
      {themeParam && <ThemeSwitcher />}
    </EffectsContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
