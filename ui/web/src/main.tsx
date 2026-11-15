// CreamFlow Control Panel (SPEC §9b): the app root. Routing by path (/, /gameplan, /accounts, ...; the server answers
// every page path with this app), the Effects setting, the server status stream, banners, the command palette, the
// shortcut sheet, toasts, the splash and page transitions (design-system/pages/shell.md).
import { StrictMode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { setNonce } from "get-nonce";
import { OctagonX, Pause, Play, Square } from "lucide-react";
import "./styles.css";
import { EffectsContext, resolveEffects, type EffectsSetting } from "./effects";
import { isTyping, useReducedMotion } from "./lib/hooks";
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
import { PlaceholderPage } from "./pages/Placeholder";

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
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [killOpen, setKillOpen] = useState(false);
  const [splash, setSplash] = useState(() => shouldShowSplash());
  const first = useRef(true);
  useEffect(() => { window.__cfMounted = true; first.current = false; }, []);
  useEffect(() => { document.documentElement.dataset.effects = fx.active; }, [fx.active]);

  const changeEffects = (s: EffectsSetting) => { setSetting(s); try { localStorage.setItem(EFFECTS_KEY, s); } catch { /* per-viewer convenience only */ } };
  const navigate = useCallback((p: PageId) => {
    const def = pageById(p);
    if (def.unavailable) return;
    setView(p);
    if (location.pathname !== pathOf(p)) history.pushState({}, "", pathOf(p));
  }, []);
  useEffect(() => { const on = () => setView(pageFromPath(location.pathname)); window.addEventListener("popstate", on); return () => window.removeEventListener("popstate", on); }, []);
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
    else if (c.id.startsWith("acct-")) navigate("accounts");
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
    { heading: "Risk", items: [{ keys: "enter", label: "Confirm KILL in its dialog" }] },
  ];

  const top = (
    <>
      <TopBar pageName={page.name} status={status} connected={connected} onEffects={changeEffects} onNavigate={navigate}
        onResult={onResult} killOpen={killOpen} setKillOpen={setKillOpen} onPalette={() => setPaletteOpen(true)} animated={animated} />
      <TickerTape items={[]} calm={!animated} note="Today's key levels appear here once the live engine runs (step 22)." />
      <Banners animated={animated} />
      {status?.synthetic && (
        <div className="flex h-6 shrink-0 items-center justify-center bg-caramel font-mono text-[11px] tracking-[0.12em] text-cream" data-synthetic>SYNTHETIC DATA · `nqbot ui --synthetic` · nothing here is a result</div>
      )}
    </>
  );

  return (
    <MotionConfig nonce={STYLE_NONCE} reducedMotion={animated ? "never" : "always"}>
    <EffectsContext.Provider value={fx}>
      <AppFrame active={view} animated={animated} hideDock={view === "home"} onNavigate={navigate} top={top}>
        <AnimatePresence mode="wait">
          <motion.div key={view} className={view === "home" ? "absolute inset-0" : "min-h-full"}
            initial={first.current || !animated ? false : view === "home" ? { opacity: 0 } : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={animated ? { opacity: 0, transition: { duration: 0.15 } } : { opacity: 0, transition: { duration: 0 } }}
            transition={{ duration: animated ? (view === "home" ? 0.2 : 0.32) : 0, ease: [0.2, 0, 0, 1] }}>
            <PlaceholderPage id={view} />
          </motion.div>
        </AnimatePresence>
      </AppFrame>
      <CommandPalette open={paletteOpen} items={commands} onSelect={onCommand} onDismiss={() => setPaletteOpen(false)} animated={animated} />
      <ShortcutsDialog groups={shortcutGroups} open={shortcutsOpen} onOpenChange={setShortcutsOpen} animated={animated}
        footer={<a href="/emergency" className="text-text-1 underline underline-offset-2">Emergency page (no JavaScript)</a>} />
      <Toaster animated={animated} />
      {splash && <Splash animated={animated} ready={status !== null || !connected} onDone={() => setSplash(false)} />}
    </EffectsContext.Provider>
    </MotionConfig>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
