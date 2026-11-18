// CreamFlow Control Panel (SPEC §9b): the app root. Routing by path (/, /gameplan, /accounts, ...; the server answers
// every page path with this app), the Effects setting, the server status stream, banners, the command palette, the
// shortcut sheet, toasts, the splash and page transitions (design-system/pages/shell.md).
// Page changes (owner 2026-11-16): the current page stays until the next page's data is loaded (lib/nav.ts), then the
// next page crossfades in with its data and plays its entrance; the page that leaves fades out where it is (its scroll
// never moves) and the next one opens at its own top; the scrolling pages share one footer that glides with the height;
// a load slower than ~300 ms shows a thin progress line; hovering a dock icon or a link to a page preloads it.
// Calm: an instant swap, no entrance.
import { StrictMode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { setNonce } from "get-nonce";
import { OctagonX, Pause, Play, Square } from "lucide-react";
import "./styles.css";
import { EffectsContext, resolveEffects, type EffectsSetting } from "./effects";
import { isTyping, useReducedMotion } from "./lib/hooks";
import { postJson } from "./lib/api";
import { AppFrame, TickerTape, TopBar } from "./shell/Shell";
import { Banners } from "./shell/Banners";
import { condition, notify } from "./lib/notify";
import { KEYED_PAGES, PAGES, pageById, pageFromPath, pathOf, type PageId } from "./shell/pages";
import { useServerStatus } from "./shell/status";
import { sendRiskAction, type ActionResult } from "./shell/RiskControls";
import { Splash, shouldShowSplash } from "./shell/Splash";
import { CommandPalette, type CommandItem } from "./components/ui/command-palette";
import { ShortcutsDialog, type ShortcutGroup } from "./components/ui/keyboard-shortcuts";
import { Toaster } from "./components/ui/toast";
import { HintLayer } from "@/components/ui/hint-layer";
import { PlaceholderPage } from "./pages/Placeholder";
import { GameplanPage } from "./pages/gameplan/GameplanPage";
import { ReviewPage } from "./pages/review/ReviewPage";
import { PerformancePage } from "./pages/performance/PerformancePage";
import { HomeScene } from "./home/HomeScene";
import { AccountsPage } from "./pages/accounts/AccountsPage";
import { SettingsPage } from "./pages/settings/SettingsPage";
import { pendingChanges } from "./lib/leave-guard";
import { FocusContext, readFocus, writeFocus } from "./lib/focus";
import { Dialog, btn } from "./components/ui/dialog";
import { BusyBar } from "./components/ui/busy-bar";
import { NavContext, preloadPath } from "./lib/nav";
import { PagesFrame } from "./shell/page";

declare global { interface Window { __cfMounted?: boolean } }

// The server's CSP allows no inline styles except those carrying this launch's nonce: Radix's scroll lock (via
// react-style-singleton / get-nonce) and Motion's popLayout (MotionConfig nonce) insert a <style> element at run time.
const STYLE_NONCE = document.querySelector<HTMLMetaElement>('meta[name="cf-nonce"]')?.content ?? "";
setNonce(STYLE_NONCE);

// Focus rings only for keyboard navigation (owner 2026-11-15): <html data-input> follows the last kind of input, and
// styles.css hides the rings while it is "pointer" (e.g. focus returning to the top bar's KILL after the dialog was
// used with the mouse). Any key that moves or acts (Tab, arrows, Enter, Space, Esc...) switches back to "keyboard".
const setInput = (v: "pointer" | "keyboard") => { if (document.documentElement.dataset.input !== v) document.documentElement.dataset.input = v; };
window.addEventListener("pointerdown", () => setInput("pointer"), true);
window.addEventListener("keydown", (e) => { if (!e.ctrlKey && !e.metaKey && !e.altKey || e.key === "Tab") setInput("keyboard"); }, true);
setInput("pointer");

const EFFECTS_KEY = "creamflow.effects";
// a page change's crossfade (the height glide under it is the tab switch's, 0.25 s)
const PAGE_FADE_S = 0.24;
// the page that leaves fades out where it is (owner 2026-11-16), quicker than the page that comes in rises (the
// pre-change entrance: 320 ms, an 8-px rise), so it is gone before the shared footer's 250-ms glide ends
const PAGE_OUT_S = 0.2, PAGE_IN_S = 0.32, PAGE_RISE_PX = 8;
const PAGE_IN_EASE = [0.2, 0, 0, 1] as const;
function readEffects(): EffectsSetting {
  try { const v = localStorage.getItem(EFFECTS_KEY); if (v === "full" || v === "calm" || v === "auto") return v; } catch { /* storage blocked */ }
  return "auto";
}

// The result of KILL / FLAT ALL / PAUSE / RESUME sent from the panel (owner 2026-11-15): "nothing was sent" with no
// engine, or a successful send, is info (fades after ~5 s); a send that failed or never reached the server is critical
// (stays until dismissed).
function announce(r: ActionResult) {
  const what = { flat: "FLAT ALL", kill: "KILL", pause: "PAUSE", resume: "RESUME" }[r.action];
  if (r.engine === "unreachable") notify({ kind: "crit", title: `${what} did not reach the server.`, text: r.message, source: "action" });
  else if (r.ok) notify({ kind: "info", title: `${what} sent.`, text: r.message, source: "action" });
  else if (r.engine === "none") notify({ kind: "info", title: `${what}: nothing was sent.`, text: r.message, source: "action" });
  else notify({ kind: "crit", title: `${what} failed.`, text: r.message, source: "action" });
}

function App() {
  const reduced = useReducedMotion();
  const [setting, setSetting] = useState<EffectsSetting>(readEffects);
  const { status, connected } = useServerStatus();
  const fx = resolveEffects(setting, reduced, status?.calm_forced ?? false);
  const animated = fx.active === "full";
  const [view, setView] = useState<PageId>(() => pageFromPath(location.pathname));
  // the first page shows once its data is loaded too (no half-filled first frame)
  const [ready, setReady] = useState(false);
  useEffect(() => { preloadPath(location.pathname).catch(() => null).finally(() => setReady(true)); }, []);
  // a page change in progress (its data loading): the latest one wins
  const navToken = useRef(0);
  const [navBusy, setNavBusy] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [killOpen, setKillOpen] = useState(false);
  const [splash, setSplash] = useState(() => shouldShowSplash());
  const first = useRef(true);
  useEffect(() => { window.__cfMounted = true; first.current = false; }, []);
  useEffect(() => { document.documentElement.dataset.effects = fx.active; }, [fx.active]);

  // the Effects setting lives in data/panel/ui.yaml (stage 2); localStorage only caches it for the first frame
  const changeEffects = (s: EffectsSetting) => {
    setSetting(s);
    try { localStorage.setItem(EFFECTS_KEY, s); } catch { /* per-viewer cache only */ }
    postJson("/api/ui", { effects: s }).catch(() => { /* no SSD: the choice holds for this browser only */ });
  };
  const fileEffects = status?.ui?.effects;
  useEffect(() => { if (fileEffects && fileEffects !== setting) { setSetting(fileEffects); try { localStorage.setItem(EFFECTS_KEY, fileEffects); } catch { /* cache */ } } }, [fileEffects]); // eslint-disable-line react-hooks/exhaustive-deps
  const [leaveTo, setLeaveTo] = useState<string | null>(null);
  const [focus, setFocusState] = useState<string | null>(readFocus);
  const setFocus = useCallback((name: string) => { setFocusState(name); writeFocus(name); }, []);
  // open a path (a page, or a page's sub-path such as /accounts/new) once its data is loaded; the page's own popstate
  // listener follows it; `state` rides on the history entry (e.g. where the account editor's back button goes)
  const go = useCallback((path: string, state: Record<string, unknown> = {}) => {
    const t = ++navToken.current;
    setNavBusy(true);
    preloadPath(path).catch(() => null).then(() => {
      if (t !== navToken.current) return;
      setNavBusy(false);
      if (location.pathname !== path) history.pushState(state, "", path);
      else history.replaceState(state, "", path);
      const next = pageFromPath(path);
      // the scroll isn't touched here: the page that leaves fades out where it is and the next one opens at its own top
      // (PagesFrame's GlideOnKey between scrolling pages, the effect below for Home)
      setView(next);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
  }, []);
  const openPath = useCallback((path: string, state?: Record<string, unknown>) => {
    if (pendingChanges() > 0) { setLeaveTo(path); return; }
    go(path, state);
  }, [go]);
  const nav = useMemo(() => ({ open: openPath }), [openPath]);
  // hovering (or focusing) a dock icon, a Home card or a link marked data-preload loads that page's data ahead
  useEffect(() => {
    const ahead = (e: Event) => {
      const el = (e.target as Element | null)?.closest?.("[data-preload], [data-dock-item], [data-home-node]");
      if (!el) return;
      const path = el.getAttribute("data-preload") ?? pathOf((el.getAttribute("data-dock-item") ?? el.getAttribute("data-home-node")) as PageId);
      preloadPath(path).catch(() => null);
    };
    window.addEventListener("pointerover", ahead);
    window.addEventListener("focusin", ahead);
    return () => { window.removeEventListener("pointerover", ahead); window.removeEventListener("focusin", ahead); };
  }, []);
  // leaving a form with unsaved changes asks first (our own dialog, never the browser's confirm(); lib/leave-guard.ts)
  const navigate = useCallback((p: PageId) => {
    const def = pageById(p);
    if (def.unavailable) return;
    if (pendingChanges() > 0) { setLeaveTo(pathOf(p)); return; }
    go(pathOf(p));
  }, [go]);
  // back / forward: the same rule (the page shown changes once the next one's data is loaded)
  useEffect(() => {
    const on = () => {
      const path = location.pathname, next = pageFromPath(path);
      const t = ++navToken.current;
      preloadPath(path).catch(() => null).then(() => { if (t === navToken.current) { setNavBusy(false); setView(next); } });
    };
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  const onResult = announce;

  // server conditions become notes once per occurrence (lib/notify.ts): losing the server is critical (kill/flat from
  // this page can't arrive), the server's own alerts keep their kind; each stays until dismissed or the condition ends
  useEffect(() => {
    condition("no-server", !connected, { kind: "crit", title: "The panel lost its server.", text: "Kill and flat from this page won't arrive. Use the emergency page, nqbot kill / nqbot flat, or Discord !kill / !flat." });
  }, [connected]);
  const activeAlerts = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!status) return;
    const ids = new Set(status.alerts.map((a) => a.id));
    for (const a of status.alerts) condition(`srv-${a.id}`, true, { kind: a.kind, title: a.title, text: a.text });
    for (const k of activeAlerts.current) if (!ids.has(k)) condition(`srv-${k}`, false, { kind: "info", title: "", text: "" });
    activeAlerts.current = ids;
  }, [status]);

  // to Home from a scrolled page: Home opens at the top of the window (shifted by the scroll) while the page fades out
  // where it is; once that page is gone the shift and the scroll go back to 0 together (nothing moves on screen)
  const homeShift = useRef<HTMLDivElement>(null);
  const prevView = useRef(view);
  useLayoutEffect(() => {
    const prev = prevView.current;
    prevView.current = view;
    if (prev === view) return;
    const main = document.getElementById("cf-main");
    if (!main) return;
    if (view !== "home") { if (prev === "home") main.scrollTop = 0; return; }
    if (!animated) { (document.querySelector("[data-pages]") as HTMLElement | null)?.style.setProperty("visibility", "hidden"); main.scrollTop = 0; return; }
    if (main.scrollTop > 0 && homeShift.current) homeShift.current.style.transform = `translateY(${main.scrollTop}px)`;
  }, [view, animated]);
  const homeSwapDone = () => {
    const el = homeShift.current;
    if (!el || !el.style.transform) return;
    el.style.transform = "";
    const main = document.getElementById("cf-main");
    if (main) main.scrollTop = 0;
  };

  const page = pageById(view);
  document.title = `CreamFlow · ${page.name}`;

  // keyboard (pages/shell.md): 1-6 pages, Ctrl+K palette, ? shortcut sheet; never while typing
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPaletteOpen((o) => !o); return; }
      if (isTyping(e) || e.ctrlKey || e.metaKey || e.altKey || paletteOpen || shortcutsOpen || killOpen) return;
      if (e.key === "?") { e.preventDefault(); setShortcutsOpen(true); return; }
      if (view !== "home" && /^[1-6]$/.test(e.key)) { const p = KEYED_PAGES[Number(e.key) - 1]; if (p) { e.preventDefault(); navigate(p.id); } }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [view, navigate, paletteOpen, shortcutsOpen, killOpen]);

  const commands: CommandItem[] = useMemo(() => [
    ...PAGES.map((p, i) => {
      const Icon = p.icon;
      const key = KEYED_PAGES.findIndex((k) => k.id === p.id);
      return { id: `page-${p.id}`, label: `Open ${p.name}`, hint: p.unavailable ?? (i === 0 ? "start page" : undefined), keywords: `go page ${p.name}`, icon: <Icon aria-hidden="true" />, shortcut: key >= 0 ? [String(key + 1)] : undefined, disabled: !!p.unavailable };
    }),
    ...(status?.accounts ?? []).map((a) => ({ id: `acct-${a.name}`, label: `Account ${a.name}`, hint: `${a.firm} · ${a.draft ? "draft" : a.phase}`, keywords: `account ${a.firm} ${a.preset ?? ""}` })),
    { id: "act-flat", label: "FLAT ALL", hint: "exit every account and ticker", keywords: "flatten exit close", icon: <Square aria-hidden="true" />, danger: true },
    { id: "act-kill", label: "KILL", hint: "asks to confirm", keywords: "stop kill engine", icon: <OctagonX aria-hidden="true" />, danger: true },
    { id: "act-pause", label: "Pause new entries", keywords: "pause", icon: <Pause aria-hidden="true" /> },
    { id: "act-resume", label: "Resume entries", keywords: "resume", icon: <Play aria-hidden="true" /> },
    { id: "emergency", label: "Open the emergency page", hint: "no JavaScript", keywords: "emergency fallback kill flat" },
    { id: "shortcuts", label: "Keyboard shortcuts", shortcut: ["?"], keywords: "help keys" },
  ], [status?.accounts]);

  const onCommand = async (c: CommandItem) => {
    setPaletteOpen(false);
    if (c.id.startsWith("page-")) navigate(c.id.slice(5) as PageId);
    else if (c.id.startsWith("acct-")) setFocus(c.id.slice(5));
    else if (c.id === "act-flat") onResult(await sendRiskAction("flat"));
    else if (c.id === "act-kill") setKillOpen(true); // the normal confirm, as from the top bar
    else if (c.id === "act-pause") onResult(await sendRiskAction("pause"));
    else if (c.id === "act-resume") onResult(await sendRiskAction("resume"));
    else if (c.id === "emergency") location.assign("/emergency");
    else if (c.id === "shortcuts") setShortcutsOpen(true);
  };

  const shortcutGroups: ShortcutGroup[] = [
    { heading: "Pages", items: KEYED_PAGES.map((p, i) => ({ keys: String(i + 1), label: `Open ${p.name}` })) },
    { heading: "Panel", items: [
      { keys: "mod+k", label: "Command palette" }, { keys: "?", label: "This sheet" }, { keys: "esc", label: "Close a dialog or leave full screen" },
      { keys: "f", label: "Full-screen chart (Review, Live)" },
    ] },
    { heading: "Review", items: [
      { keys: "t", label: "Take" }, { keys: "s", label: "Skip" }, { keys: "mod+enter", label: "Answer and reveal" },
      { keys: "space", label: "Play / pause the replay" }, { keys: ["left", "right"], label: "Step back / forward (1 bar at 1×, 2 at 2×, 4 at 4×; playback goes on)" },
      { keys: "home", label: "Restart the replay" }, { keys: "end", label: "To the decision (after the answer: the end of the path)" },
      { keys: "a", label: "Every level at full strength (the chart's Focus off)" },
      { keys: ["p", "n"], label: "Previous / next card" }, { keys: "l", label: "Later: the next card not answered" },
    ] },
    { heading: "Risk", items: [{ keys: "enter", label: "Confirm KILL in its dialog" }] },
  ];

  const top = (
    <>
      <TopBar pageName={page.name} status={status} connected={connected} onEffects={changeEffects} onNavigate={navigate}
        onResult={onResult} killOpen={killOpen} setKillOpen={setKillOpen} onPalette={() => setPaletteOpen(true)} animated={animated}
        onOpenPath={openPath} focus={focus} onFocus={setFocus} />
      <TickerTape items={[]} calm={!animated} note="Today's key levels appear here once the live engine runs (step 22)." />
      <Banners animated={animated} fold={view !== "home"} />
      {/* a page change whose data takes longer than ~300 ms: a thin line under the top bar (it never moves the layout) */}
      <div className="relative z-[29] h-0"><BusyBar busy={navBusy} animated={animated} label="Loading the page" className="top-0" data-nav-loading /></div>
      {status?.synthetic && (
        <div className="flex h-6 shrink-0 items-center justify-center bg-caramel font-mono text-[11px] tracking-[0.12em] text-cream" data-synthetic>SYNTHETIC DATA · nqbot ui --synthetic · nothing here is a result</div>
      )}
    </>
  );

  return (
    <MotionConfig nonce={STYLE_NONCE} reducedMotion={animated ? "never" : "always"}>
    <EffectsContext.Provider value={fx}>
    <FocusContext.Provider value={{ focus, setFocus }}>
    <NavContext.Provider value={nav}>
      <AppFrame active={view} animated={animated} hideDock={view === "home"} onNavigate={navigate} top={top}>
        {/* the pages crossfade (both on screen at once, never a blank gap); Home fills the whole content area (its bottom
            row shares the date block's line, owner 2026-11-15); the scrolling pages share one frame and footer */}
        {ready && (
        <AnimatePresence initial={false} onExitComplete={homeSwapDone}>
          {view === "home" ? (
            <motion.div key="home" className="absolute inset-0" data-view="home"
              initial={animated ? { opacity: 0 } : false} animate={{ opacity: 1 }}
              exit={{ opacity: 0, transition: { duration: animated ? PAGE_OUT_S : 0, ease: "easeOut" } }} transition={{ duration: animated ? PAGE_FADE_S : 0, ease: "easeOut" }}>
              <div ref={homeShift} className="absolute inset-0" data-shift>
                <HomeScene calm={!animated} onOpenPage={navigate} blocked={paletteOpen || shortcutsOpen || killOpen} />
              </div>
            </motion.div>
          ) : (
            <motion.div key="pages" className="min-h-full" data-pages
              initial={animated ? { opacity: 0 } : false} animate={{ opacity: 1 }}
              exit={{ opacity: 0, transition: { duration: animated ? PAGE_OUT_S : 0, ease: "easeOut" } }} transition={{ duration: animated ? PAGE_FADE_S : 0, ease: "easeOut" }}>
              <PagesFrame k={view}>
                {/* each page plays its entrance when it is revealed (owner 2026-11-16: the data is already in place): it rises
                    in, and its own entrance (Performance's numbers rolling in, its staggered cards) runs as it mounts */}
                <AnimatePresence initial={animated} mode="popLayout">
                  <motion.div key={view} data-view={view}
                    // from Home the frame itself fades in, so the page only rises (a second fade would dim it twice)
                    initial={animated ? (prevView.current === "home" ? { y: PAGE_RISE_PX } : { opacity: 0, y: PAGE_RISE_PX }) : false} animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, transition: { duration: animated ? PAGE_OUT_S : 0, ease: "easeOut" } }}
                    transition={{ duration: animated ? PAGE_IN_S : 0, ease: PAGE_IN_EASE }}>
                    {/* GlideOnKey moves this (not the page) to keep a leaving page's scroll and open the next at its top */}
                    <div data-shift>
                      {view === "accounts" ? <AccountsPage />
                        : view === "settings" ? <SettingsPage onEffects={changeEffects} />
                        : view === "performance" ? <PerformancePage />
                        : view === "review" ? <ReviewPage />
                        : view === "gameplan" ? <GameplanPage />
                        : <PlaceholderPage id={view} />}
                    </div>
                  </motion.div>
                </AnimatePresence>
              </PagesFrame>
            </motion.div>
          )}
        </AnimatePresence>
        )}
      </AppFrame>
      <CommandPalette open={paletteOpen} items={commands} onSelect={onCommand} onDismiss={() => setPaletteOpen(false)} animated={animated} />
      <ShortcutsDialog groups={shortcutGroups} open={shortcutsOpen} onOpenChange={setShortcutsOpen} animated={animated}
        footer={<a href="/emergency" className="text-text-1 underline underline-offset-2">Emergency page (no JavaScript)</a>} />
      <Toaster animated={animated} />
      <HintLayer />
      <Dialog open={leaveTo !== null} onOpenChange={(v) => { if (!v) setLeaveTo(null); }} title="Leave without" titleItalic="saving?"
        description={`${pendingChanges()} unsaved change${pendingChanges() === 1 ? "" : "s"} on this page will be lost.`}
        footer={<>
          <button type="button" className={btn.secondary} onClick={() => setLeaveTo(null)}>Stay</button>
          <button type="button" className={btn.primary} onClick={() => { const p = leaveTo; setLeaveTo(null); if (p) go(p); }}>Discard and leave</button>
        </>} />
      {splash && <Splash animated={animated} ready={status !== null || !connected} onDone={() => setSplash(false)} />}
    </NavContext.Provider>
    </FocusContext.Provider>
    </EffectsContext.Provider>
    </MotionConfig>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
