// Performance mock (design-system/pages/performance.md) with MOCK DATA. Picks used: KPI Tile (40086), Animated Tabs
// (24930), Spotlight (35180), Border Beam Panel (23408), Halftone Wordmark Footer (37628, the owner's Halden footer),
// the P&L calendar after the reference layout, Lightweight Charts for equity/drawdown.
import { useEffect, useRef, useState } from "react";
import { motion, type Variants } from "framer-motion";
import { AnimatePresence } from "framer-motion";
import { CalendarRange, Check, ChevronDown, Download, Wallet } from "lucide-react";
import KpiTile from "@/components/ui/kpi-tile";
import AnimatedTabs from "@/components/ui/animated-tabs";
import { Spotlight } from "@/components/ui/spotlight";
import { BorderBeamPanel } from "@/components/ui/border-beam-panel";
import HalftoneWordmarkFooter from "@/components/ui/halftone-wordmark-footer";
import { DrawdownChart, EquityChart, MonteCarloFan } from "./charts";
import { PnlCalendar } from "./PnlCalendar";
import { TradesTable } from "./TradesTable";
import { ACCOUNTS, MONTH_LABELS, viewOf, type AccountSel } from "./data";
import { money, rr, useAnimated } from "@/effects";

// the footer word's weight from the footer's width: SemiBold (600) at the 960-px window (≈ 945 px of footer), Black (900)
// at full screen (1920 px, ≈ 1905 px of footer), straight in between (owner 2026-11-15)
const FOOTER_W_NARROW = 945, FOOTER_W_FULL = 1905, FOOTER_WEIGHT_NARROW = 600, FOOTER_WEIGHT_FULL = 900;
function footerWeight(width: number) {
  const t = Math.min(1, Math.max(0, (width - FOOTER_W_NARROW) / (FOOTER_W_FULL - FOOTER_W_NARROW)));
  return Math.round((FOOTER_WEIGHT_NARROW + t * (FOOTER_WEIGHT_FULL - FOOTER_WEIGHT_NARROW)) / 10) * 10;
}

const TABS = [
  { id: "equity", label: "Equity" }, { id: "drawdown", label: "Drawdown" }, { id: "montecarlo", label: "Monte Carlo" },
  { id: "calendar", label: "Calendar" }, { id: "trades", label: "Trades" },
];

function Scramble({ text, animated }: { text: string; animated: boolean }) {
  // text scramble on load (after 21st.dev 16620): letters settle left to right in ~0.6 s; Calm shows the text at once
  const [out, setOut] = useState(animated ? "" : text);
  useEffect(() => {
    if (!animated) { setOut(text); return; }
    const glyphs = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/";
    let f = 0;
    const id = setInterval(() => {
      f++;
      setOut(text.split("").map((c, i) => (c === " " || i < f / 1.6 ? c : glyphs[(i * 7 + f * 3) % glyphs.length])).join(""));
      if (f / 1.6 > text.length) { clearInterval(id); setOut(text); }
    }, 16);
    return () => clearInterval(id);
  }, [text, animated]);
  return <span aria-label={text}>{out}</span>;
}

/** The account filter (every view follows it). Drawdown and Monte Carlo are per account by nature: with All accounts
 *  they show one panel per account, each under its own rule. */
function AccountSelect({ value, onChange, animated }: { value: AccountSel; onChange: (v: AccountSel) => void; animated: boolean }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("pointerdown", off); window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("pointerdown", off); window.removeEventListener("keydown", esc); };
  }, [open]);
  const opts: { id: AccountSel; name: string; sub: string }[] = [
    { id: "all", name: "All accounts", sub: `${ACCOUNTS.length} accounts` },
    ...ACCOUNTS.map((a) => ({ id: a.id, name: a.id, sub: `${a.preset} · ${a.phase}` })),
  ];
  const cur = opts.find((o) => o.id === value)!;
  return (
    <div ref={box} className="relative">
      <button aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        className="flex h-8 items-center gap-2 rounded-sm border border-line-strong bg-bg-2 px-2.5 text-[13px] text-text-1"><Wallet className="size-3.5 text-text-3" />{cur.name}<ChevronDown className="size-3.5 text-text-3" /></button>
      <AnimatePresence>
        {open && (
          <motion.ul role="listbox" aria-label="Account" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }}
            transition={{ duration: animated ? 0.14 : 0 }}
            className="absolute right-0 z-40 mt-1 w-[260px] overflow-hidden rounded-md border border-line-strong bg-bg-3 py-1 shadow-[0_12px_32px_rgba(0,0,0,.55)]">
            {opts.map((o) => (
              <li key={o.id} role="option" aria-selected={o.id === value}>
                <button onClick={() => { onChange(o.id); setOpen(false); }} className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-white/[.04]">
                  <span className="min-w-0 flex-1"><span className="block text-[13px] text-text-1">{o.name}</span><span className="block text-[11.5px] text-text-3">{o.sub}</span></span>
                  {o.id === value && <Check className="size-3.5 text-gold" />}
                </button>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  );
}

export function PerformancePage({ initialTab = "equity", initialAccount = "all" }: { initialTab?: string; initialAccount?: AccountSel }) {
  const animated = useAnimated();
  const [tab, setTab] = useState(initialTab);
  const [unit, setUnit] = useState<"usd" | "r">("usd");
  const [toast, setToast] = useState<string | null>(null);
  const [account, setAccount] = useState<AccountSel>(initialAccount);
  const view = viewOf(account), STATS = view.stats, monthly = view.monthly;
  const accountIds = account === "all" ? ACCOUNTS.map((a) => a.id) : [account];
  const net = monthly((ds) => ds.reduce((a, d) => a + d.usd, 0));
  const tr = monthly((ds) => ds.reduce((a, d) => a + d.trades.length, 0));
  // average net R per trade, per month (owner 2026-11-15: replaces net R)
  const rpt = monthly((ds) => { const n = ds.reduce((a, d) => a + d.trades.length, 0); return n ? ds.reduce((a, d) => a + d.r, 0) / n : 0; });
  const wr = monthly((ds) => { const t = ds.flatMap((d) => d.trades); const w = t.filter((x) => x.usd > 0).length, l = t.filter((x) => x.usd < 0).length; return w + l ? (100 * w) / (w + l) : 0; });
  const pf = monthly((ds) => { const t = ds.flatMap((d) => d.trades); const g = t.filter((x) => x.usd > 0).reduce((a, x) => a + x.usd, 0), l = -t.filter((x) => x.usd < 0).reduce((a, x) => a + x.usd, 0); return l ? g / l : g ? 3 : 0; });
  const dd = monthly((ds) => { let c = 0, p = 0, m = 0; for (const d of ds) { c += d.usd; p = Math.max(p, c); m = Math.min(m, c - p); } return m; });
  // last month vs the average of the earlier months
  const ch = (a: number[]) => { const x = a.at(-1)!, y = a.slice(0, -1).reduce((p, v) => p + v, 0) / (a.length - 1); return y ? ((x - y) / Math.abs(y)) * 100 : 0; };
  const stagger = { hidden: {}, show: { transition: { staggerChildren: animated ? 0.05 : 0 } } };
  const item: Variants = { hidden: animated ? { opacity: 0, y: 8 } : { opacity: 1 }, show: { opacity: 1, y: 0, transition: { duration: animated ? 0.32 : 0, ease: [0.2, 0, 0, 1] as const } } };

  return (
    <div className="flex min-h-full flex-col" style={{ marginBottom: "calc(-1 * var(--dock-pad, 0px))" }}>
      <motion.div variants={stagger} initial="hidden" animate="show" className="flex-1 px-6 pb-10 pt-6 min-[1440px]:px-8">
        <motion.div variants={item} className="flex flex-wrap items-end gap-4">
          <div className="min-w-0">
            <div className="eyebrow"><Scramble text="CREAMFLOW / PERFORMANCE" animated={animated} /></div>
            <h1 className="mt-1.5 font-display text-[38px] leading-[40px] tracking-[-0.01em] text-text-1">Performance<br /><span className="italic text-cream">Backtest, months 1–6</span></h1>
            <p className="mt-1.5 text-[15px] text-text-2">Net of commissions, New York trading dates. Walk-forward months show totals only.</p>
          </div>
          <span className="mb-1 rounded-full border border-warn/40 bg-warn/10 px-2.5 py-0.5 font-mono text-[10.5px] tracking-[0.08em] text-warn">MOCK DATA</span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <AccountSelect value={account} onChange={setAccount} animated={animated} />
            <button className="flex h-8 items-center gap-2 rounded-sm border border-line-strong bg-bg-2 px-2.5 text-[13px] text-text-1">Development<ChevronDown className="size-3.5 text-text-3" /></button>
            <button className="flex h-8 items-center gap-2 rounded-sm border border-line-strong bg-bg-2 px-2.5 font-mono text-[12px] text-text-1"><CalendarRange className="size-3.5 text-text-3" />Oct 1, 2025 – Mar 31, 2026</button>
            <AnimatedTabs variant="segment" layoutId="unit" animated={animated} activeTab={unit} onChange={(v) => setUnit(v as "usd" | "r")} tabs={[{ id: "usd", label: "$" }, { id: "r", label: "R" }]} className="h-8 w-[84px]" />
            <button onClick={() => { setToast(`Exported ${STATS.trades} trades (mock)`); setTimeout(() => setToast(null), 2200); }}
              className="flex h-8 items-center gap-2 rounded-sm bg-gold px-3 text-[13px] font-medium text-[#081a1b] transition-transform duration-150 hover:brightness-105 active:scale-[.98]"><Download className="size-3.5" />Export CSV</button>
          </div>
        </motion.div>

        <motion.div variants={item} className="mt-6 grid grid-cols-2 gap-3 min-[1200px]:grid-cols-3 min-[1400px]:grid-cols-6">
          {[
            { label: "Net P&L", value: STATS.net, fmt: (v: number) => money(v), trend: net, delta: ch(net) },
            { label: "Trades", value: STATS.trades, fmt: (v: number) => Math.round(v).toString(), trend: tr, delta: ch(tr) },
            { label: "Net R per trade", value: STATS.rPerTrade, fmt: (v: number) => rr(v), trend: rpt, delta: ch(rpt) },
            { label: "Win rate", value: STATS.winRate * 100, fmt: (v: number) => `${v.toFixed(1)}%`, trend: wr, delta: ch(wr) },
            { label: "Profit factor", value: STATS.profitFactor, fmt: (v: number) => v.toFixed(2), trend: pf, delta: ch(pf) },
            { label: "Max drawdown", value: STATS.maxDD, fmt: (v: number) => money(v), trend: dd, delta: ch(dd.map((x) => -x)), lower: true },
          ].map((k) => (
            <Spotlight key={k.label} enabled={animated} className="rounded-md">
              <KpiTile label={k.label} value={k.value} format={k.fmt} trend={k.trend} trendLabels={MONTH_LABELS} delta={k.delta} deltaLabel="Mar vs Oct–Feb average" lowerIsBetter={k.lower} animated={animated} className="h-full bg-bg-2/90" />
            </Spotlight>
          ))}
        </motion.div>

        <motion.div variants={item} className="mt-7">
          <div className="flex flex-wrap items-center gap-3">
            <AnimatedTabs variant="underline" layoutId="perf-tabs" animated={animated} activeTab={tab} onChange={setTab} tabs={TABS} />
            <div className="ml-auto flex items-center gap-2 font-mono text-[11px] text-text-3">
              <span className="rounded-xs border border-line-strong px-1.5 py-0.5">NQ · all accounts</span>
              <span className="rounded-xs border border-line-strong px-1.5 py-0.5">Eastern time</span>
              <span className="rounded-xs border border-line-strong px-1.5 py-0.5">read only</span>
            </div>
          </div>
          <BorderBeamPanel reducedMotion={!animated} glow={false} className="mt-4 p-4" radius={8}>
            {/* drawdown and Monte Carlo are per account (each under its own rule): one panel per account with All */}
            {(tab === "drawdown" || tab === "montecarlo") ? (
              <div className={accountIds.length > 1 ? "grid gap-6 min-[1280px]:grid-cols-2" : ""}>
                {accountIds.map((id) => (
                  <div key={id} className={accountIds.length > 1 ? "h-[400px]" : "h-[420px]"}>
                    {tab === "drawdown" ? <DrawdownChart id={id} /> : <MonteCarloFan id={id} />}
                  </div>
                ))}
              </div>
            ) : (
              <div className={tab === "calendar" || tab === "trades" ? "" : "h-[380px]"}>
                {tab === "equity" && <EquityChart unit={unit} animated={animated} view={view} />}
                {tab === "calendar" && <PnlCalendar unit={unit} animated={animated} days={view.days} />}
                {tab === "trades" && <TradesTable trades={view.trades} />}
              </div>
            )}
          </BorderBeamPanel>
        </motion.div>
      </motion.div>

      {/* the dot-matrix footer ends the page (owner 2026-11-15): "cream" in heavy Geist, Black (900) at full screen down
          to SemiBold (600) at 960 px, sized from its own box; the page ends inside the letters (a third of their height
          hidden by the bottom edge, owner 2026-11-15), so it cancels the dock's bottom padding (--dock-pad) */}
      <HalftoneWordmarkFooter
        wordmark="cream" label="CreamFlow · NQ" meta="Local control panel · 127.0.0.1" columns={[]} backToTop={false}
        palette={{ background: "#111111", ink: "#ffe0c2", warm: "#ff9a3c" }} fontFamily='"Geist Variable", ui-sans-serif, sans-serif'
        fontWeight={footerWeight} fit="inset" fitHeight={{ gapRatio: 0.18, minGap: 32 }}
        monoFamily='"Geist Mono Variable", monospace' crop={1 / 3} grain={0.25} interactive={animated} height="320px"
        className="shrink-0" />

      {toast && <div role="status" style={{ bottom: "var(--toast-bottom, 24px)" }} className="fixed right-6 z-50 rounded-md border border-line-strong bg-bg-3 px-4 py-2.5 text-[13px] text-text-1 shadow-[0_12px_32px_rgba(0,0,0,.55)]">{toast}</div>}
    </div>
  );
}
