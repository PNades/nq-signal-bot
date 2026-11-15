// Style tile (step 14a part 0, step 4): the design system on one page — brand, Cocoa Amber tokens, type, controls,
// feedback, the risk-first controls, the chart palette, and the showpieces (tilt card, border beam, spotlight,
// flip-disk countdown, delta ticker, burst, dot-matrix empty state). MOCK values only.
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Crown, Info, OctagonAlert, OctagonX, Pause, Trash2 } from "lucide-react";
import { LogoMark, Wordmark } from "@/shell/Shell";
import AnimatedTabs from "@/components/ui/animated-tabs";
import { TiltCard } from "@/components/ui/tilt-card";
import { BorderBeamPanel } from "@/components/ui/border-beam-panel";
import { Spotlight } from "@/components/ui/spotlight";
import { FlipDiskDisplay } from "@/components/ui/flip-disk-matrix";
import { DeltaNumberTicker } from "@/components/ui/delta-number-ticker";
import { Burst, type BurstHandle } from "@/components/ui/burst";
import { DotMatrixText } from "@/components/ui/dot-text";
import { useAnimated } from "@/effects";
import { cn } from "@/lib/utils";

const SWATCHES: [string, string][] = [
  ["bg-0", "--color-bg-0"], ["bg-1", "--color-bg-1"], ["bg-2", "--color-bg-2"], ["bg-3", "--color-bg-3"], ["line", "--color-line"], ["line-strong", "--color-line-strong"],
  ["text-1", "--color-text-1"], ["text-2", "--color-text-2"], ["cream / gold", "--color-cream"], ["cocoa", "--color-caramel"], ["ember (glows)", "--color-ember"],
  ["ok · profit ▲+", "--color-pnl-up"], ["loss ▼−", "--color-pnl-down"], ["warn", "--color-warn"], ["crit", "--color-crit"], ["crit-fill", "--color-crit-fill"], ["info", "--color-info"],
];

function Card({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-md border border-line-strong/70 bg-bg-2 p-4", className)}>
      <div className="eyebrow mb-3">{title}</div>
      {children}
    </section>
  );
}

function nextWindowText() {
  // recomputed from the clock on every tick, never counted down (pages/shell.md)
  const p = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false, weekday: "short" }).formatToParts(new Date());
  const g = (t: string) => p.find((x) => x.type === t)?.value ?? "";
  const mins = Number(g("hour")) * 60 + Number(g("minute")), wd = g("weekday");
  if (wd !== "Sat" && wd !== "Sun" && mins < 9 * 60 + 45) { const d = 9 * 60 + 45 - mins; return `${String(Math.floor(d / 60)).padStart(2, "0")}:${String(d % 60).padStart(2, "0")}`; }
  return "MON 9:30";
}

export function StyleTile() {
  const animated = useAnimated();
  const burst = useRef<BurstHandle>(null);
  const [seg, setSeg] = useState("normal");
  const [val, setVal] = useState(2601.25);
  const [flip, setFlip] = useState(nextWindowText());
  const [risk, setRisk] = useState(500);
  const [on, setOn] = useState(true);
  useEffect(() => {
    const a = setInterval(() => setVal((v) => +(v + (Math.random() < 0.55 ? 1 : -1) * (40 + Math.random() * 180)).toFixed(2)), 2600);
    const b = setInterval(() => setFlip(nextWindowText()), 15000);
    return () => { clearInterval(a); clearInterval(b); };
  }, []);

  return (
    <div className="px-6 pb-12 pt-6 min-[1440px]:px-8">
      <div className="eyebrow">CREAMFLOW / STYLE TILE</div>
      <h1 className="mt-1.5 font-display text-[38px] leading-[40px] text-text-1">Style tile<br /><span className="italic text-cream">Cocoa Amber, Instrument Serif · Geist · Geist Mono</span></h1>
      <p className="mt-1.5 text-[15px] text-text-2">design-system/MASTER.md as built: tokens, type, controls and showpieces. Mock values.</p>

      <div className="mt-6 grid grid-cols-1 gap-3 min-[1100px]:grid-cols-12">
        <Card title="Brand" className="min-[1100px]:col-span-4">
          <div className="flex items-center gap-4">
            <LogoMark size={56} />
            <Wordmark className="text-[56px]" />
          </div>
          <div className="mt-3 flex items-center gap-3 text-[12px] text-text-3"><img src="/favicon.svg" alt="" className="size-8 shrink-0" /><span>Favicon. One word, capital C and F; <span className="font-mono">CREAMFLOW</span> in eyebrows.</span></div>
        </Card>

        <Card title="Type" className="min-[1100px]:col-span-8">
          <div className="grid grid-cols-1 gap-x-6 gap-y-1 min-[1380px]:grid-cols-2">
            <div className="font-display text-[38px] leading-[40px]">Page title <span className="italic text-cream">second line</span></div>
            <div className="font-display text-[26px] leading-[30px]">Section title 26</div>
            <div className="text-[18px] font-semibold">Card title 18 · Geist</div>
            <div className="text-[14px] text-text-1">Body 14 · secondary <span className="text-text-2">text-2</span></div>
            <div className="whitespace-nowrap font-mono text-[24px] leading-9">+$2,601.25 <span className="text-text-2">21,437.25</span></div>
            <div className="eyebrow self-center">EYEBROW · MONO 11 · +0.12EM</div>
          </div>
        </Card>

        <Card title="Colour tokens (Cocoa Amber + CreamFlow state and P&L)" className="min-[1100px]:col-span-12">
          <div className="grid grid-cols-3 gap-2 min-[1100px]:grid-cols-9">
            {SWATCHES.map(([n, v]) => (
              <div key={n} className="overflow-hidden rounded-sm border border-line-strong/60">
                <div className="h-9" style={{ background: `var(${v})` }} />
                <div className="bg-bg-1 px-2 py-1 font-mono text-[10.5px] text-text-2">{n}</div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Risk first: always visible, never animated in" className="min-[1100px]:col-span-5">
          <div className="flex flex-wrap items-center gap-2">
            <button className="flex h-9 items-center gap-2 rounded-sm bg-crit-fill px-4 text-[13px] font-semibold tracking-[0.04em] text-white"><OctagonX className="size-4" />FLAT ALL</button>
            <span className="flex h-7 items-center gap-2 rounded-full border border-ok/40 px-3 font-mono text-[11px] tracking-[0.08em] text-ok"><span className="size-1.5 rounded-full bg-ok" />ENGINE LIVE</span>
            <span className="flex h-7 items-center gap-2 rounded-full border border-warn/40 px-3 font-mono text-[11px] tracking-[0.08em] text-warn"><Pause className="size-3" />PAUSED</span>
            <span className="flex h-7 items-center gap-2 rounded-full border border-crit/50 px-3 font-mono text-[11px] tracking-[0.08em] text-crit"><OctagonAlert className="size-3" />KILLED</span>
          </div>
          <div className="mt-3 flex items-start gap-2 rounded-sm border-l-2 border-crit bg-crit/10 px-3 py-2 text-[13px] text-text-1"><OctagonAlert className="mt-0.5 size-4 shrink-0 text-crit" /><span><b>mffur1 unknown:</b> exit not acknowledged after retries. Check Tradovate, then <span className="font-mono">!resolve</span>.</span></div>
          <div className="mt-2 flex items-start gap-2 rounded-sm border-l-2 border-warn bg-warn/10 px-3 py-2 text-[13px] text-text-1"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" />Skylit map QQQ stale 6 s: no new entries on its levels.</div>
          <div className="mt-2 flex items-start gap-2 rounded-sm border-l-2 border-info bg-info/10 px-3 py-2 text-[13px] text-text-2"><Info className="mt-0.5 size-4 shrink-0 text-info" />Settings apply between trades only.</div>
        </Card>

        <Card title="Controls" className="min-[1100px]:col-span-7">
          <AnimatedTabs variant="pill" layoutId="tile-pill" animated={animated} activeTab={seg} onChange={setSeg} tabs={[{ id: "aggr", label: "Aggressive" }, { id: "normal", label: "Normal" }, { id: "cons", label: "Conservative" }, { id: "protect", label: "Protect" }]} className="mb-4" />
          <div className="grid grid-cols-1 gap-4 min-[1100px]:grid-cols-2">
            <div className="space-y-3">
              <label className="block">
                <span className="text-[12px] text-text-2">Risk per trade</span>
                <div className="mt-1 flex items-center gap-3">
                  <input type="range" min={100} max={833} value={risk} onChange={(e) => setRisk(+e.target.value)} className="h-1 flex-1 accent-[#ffe0c2]" aria-label="Risk per trade" />
                  <span className="flex h-8 w-[110px] items-center rounded-sm border border-line-strong bg-bg-1 px-2 font-mono text-[13px]">${risk}<span className="ml-auto text-text-3">/ $833</span></span>
                </div>
                <span className="mt-1 block text-[11.5px] text-text-3">cap: drawdown ÷ 3 = $833</span>
              </label>
              <label className="flex items-center gap-3 text-[13px]">
                <button role="switch" aria-checked={on} onClick={() => setOn(!on)} className={cn("relative h-5 w-9 shrink-0 rounded-full border transition-colors duration-150", on ? "border-gold/60 bg-gold/30" : "border-line-strong bg-bg-3")}>
                  <span className={cn("absolute left-0 top-[2px] size-3.5 rounded-full transition-transform duration-150", on ? "translate-x-[18px] bg-cream" : "translate-x-[2px] bg-text-3")} />
                </button>
                Breakeven on
              </label>
            </div>
            <div className="space-y-3">
              <label className="block"><span className="text-[12px] text-text-2">Max drawdown</span>
                <div className="mt-1 flex h-8 items-center rounded-sm border border-line-strong bg-bg-1 px-2 font-mono text-[13px]">2,500<span className="ml-auto text-text-3">$</span></div></label>
              <label className="block"><span className="text-[12px] text-text-2">Account name</span>
                <div className="mt-1 flex h-8 items-center rounded-sm border border-crit/70 bg-bg-1 px-2 font-mono text-[13px]">MFFU R1</div>
                <span className="mt-1 flex items-center gap-1 text-[11.5px] text-crit"><OctagonAlert className="size-3" />Lowercase letters and digits only</span></label>
              <div className="flex gap-2">
                <button className="h-8 rounded-sm bg-gold px-3 text-[13px] font-medium text-[#081a1b]">Save</button>
                <button className="h-8 rounded-sm border border-line-strong bg-bg-2 px-3 text-[13px]">Cancel</button>
                <button className="flex h-8 items-center gap-1.5 whitespace-nowrap rounded-sm border border-crit/50 px-3 text-[13px] text-crit"><Trash2 className="size-3.5" />Hold to delete</button>
              </div>
            </div>
          </div>
        </Card>

        <Card title="Account card · Tilt Card 12244 (6°, 1.02; off in Calm and when in danger)" className="min-[1100px]:col-span-4">
          <TiltCard tiltLimit={animated ? 6 : 0} scale={animated ? 1.02 : 1} spotlight={animated} effect="gravitate" className="rounded-md border border-line-strong/70 bg-bg-1 p-4">
            <div className="flex items-center justify-between"><span className="font-mono text-[14px]">mffur1</span><span className="rounded-full border border-gold/40 px-2 font-mono text-[10px] text-gold">EVALUATION</span></div>
            <div className="mt-3 flex items-center gap-4">
              <svg viewBox="0 0 64 64" className="size-16 -rotate-90" aria-label="Drawdown used 50%"><circle cx="32" cy="32" r="26" fill="none" stroke="#2a2a2a" strokeWidth="6" /><circle cx="32" cy="32" r="26" fill="none" stroke="#ffe0c2" strokeWidth="6" strokeDasharray={`${0.5 * 163} 163`} strokeLinecap="round" /></svg>
              <div><div className="eyebrow">Drawdown margin</div><div className="font-mono text-[22px]">$1,240</div><div className="text-[12px] text-text-3">2.5 lives · risk $500</div></div>
            </div>
          </TiltCard>
        </Card>

        <Card title="Border beam 23408 · spotlight 35180" className="min-[1100px]:col-span-4">
          <BorderBeamPanel reducedMotion={!animated} glow={false} className="p-4">
            <Spotlight enabled={animated} className="rounded-sm p-1">
              <div className="eyebrow">Net R, months 1–6</div>
              <div className="mt-1 font-mono text-[28px] text-pnl-up"><span className="mr-1 text-[.6em]">▲</span>+18.40R</div>
              <div className="text-[12px] text-text-3">backtest · mock data</div>
            </Spotlight>
          </BorderBeamPanel>
        </Card>

        <Card title="Delta number ticker 44850" className="min-[1100px]:col-span-4">
          <DeltaNumberTicker label="Est. P&L today" value={val} format={{ style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }} valueClassName="font-mono text-[30px]" duration={animated ? 420 : 0} />
        </Card>

        <Card title="Flip-disk 24220 · next setup window (never for risk)" className="min-[1100px]:col-span-6">
          <FlipDiskDisplay text={flip} animated={animated} label={`Next setup window in ${flip}`} />
        </Card>

        <Card title="Chart palette: candles, Pika / Barney orbs, King" className="min-[1100px]:col-span-6">
          <svg viewBox="0 0 520 150" className="h-[150px] w-full" role="img" aria-label="Chart palette sample">
            {[30, 46, 62, 78, 94, 110, 126, 142, 158, 174, 190, 206].map((x, i) => {
              const up = [1, 1, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0][i], o = 70 + Math.sin(i) * 18, c = o + (up ? -14 : 14);
              return <g key={x}><line x1={x} x2={x} y1={Math.min(o, c) - 8} y2={Math.max(o, c) + 8} stroke={up ? "#efe6d2" : "#8494e6"} /><rect x={x - 5} y={Math.min(o, c)} width={10} height={Math.abs(c - o)} fill={up ? "#efe6d2" : "#8494e6"} /></g>;
            })}
            {[[260, 40, 0.5, 1], [300, 60, 0.3, 0], [340, 50, 0.9, 1], [380, 95, 0.4, 0], [420, 30, 0.25, 1], [460, 100, 0.6, 0]].map(([x, y, s, pika], i) => (
              <g key={i}><circle cx={x} cy={y} r={6 + 12 * s} fill={pika ? "#f2b950" : "#dd9be0"} opacity={0.18 + 0.3 * s} /><circle cx={x} cy={y} r={3 + 6 * s} fill={pika ? "#f2b950" : "#dd9be0"} opacity={0.5 + 0.5 * s} /></g>
            ))}
            <g><circle cx={340} cy={50} r={26} fill="none" stroke="#fff1dc" strokeOpacity={0.7} strokeWidth={2} /><text x={340} y={22} textAnchor="middle" fontSize={10} fill="#eeeeee" fontFamily="Geist Mono Variable">KING</text></g>
            <text x={30} y={140} fontSize={10} fill="#b4b4b4" fontFamily="Geist Mono Variable">up cream · down periwinkle</text>
            <text x={260} y={140} fontSize={10} fill="#b4b4b4" fontFamily="Geist Mono Variable">Pika gold + · Barney orchid − · King ring</text>
          </svg>
          <div className="mt-1 flex items-center gap-2 text-[12px] text-text-3"><Crown className="size-3.5 text-cream" />King-switch marker · <Check className="size-3.5 text-ok" />held ● · broke ✕ · timeout ◌</div>
        </Card>

        <Card title="Moment · Burst 33152 (payout requested)" className="relative overflow-hidden min-[1100px]:col-span-4">
          <Burst ref={burst} announce="Payout requested" enabled={animated} />
          <p className="text-[13px] text-text-2">A celebration for an evaluation passed or a payout requested; a static badge in Calm.</p>
          <button onClick={() => burst.current?.fire()} className="mt-3 h-9 rounded-sm bg-gold px-4 text-[13px] font-medium text-[#081a1b] transition-transform duration-150 active:scale-[.97]">Request payout</button>
        </Card>

        <Card title="Empty state · Dot Matrix Text 25484" className="min-[1100px]:col-span-8">
          <div className="h-[120px]">
            <DotMatrixText className="h-full" text={["No trades yet", "Waiting for step 24"]} transition={animated ? "scramble" : "none"} cycleInterval={animated ? 3500 : 0} activeColor="#ffe0c2" inactiveColor="#2a2a2a" dotSize={3} gap={2} />
          </div>
        </Card>
      </div>
    </div>
  );
}
