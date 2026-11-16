// Strategy settings (SPEC §9b item 2, §6.8; design-system/pages/settings.md): a sticky section list beside grouped panels
// (max 880 px). Per account: the risk profile (Aggressive / Normal / Conservative / Protect / Custom) with each profile's
// backtested stats ("n/a" until the step-15 sweep writes models/profiles.yaml), the Custom risk $ slider capped at
// drawdown ÷ N and the threshold sliders, auto-protect for funded accounts, lives mode, λ and Re-run sweep (from step
// 15). Global: gate_pass_rate_min, the session and setup windows and the news blackout (read-only, owner rules),
// Effects & display, and a danger zone. Per-account changes save through the unsaved-changes bar and apply between
// trades only (§9b).
import { useCallback, useEffect, useMemo, useState } from "react";
import { Gauge, HardDrive, Lock, RotateCw, ShieldCheck, Sparkles, Leaf, SunMoon } from "lucide-react";
import { ApiError, getJson, postJson } from "@/lib/api";
import { money0 } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useAnimated, useEffectsState, type EffectsSetting } from "@/effects";
import { registerLeaveGuard } from "@/lib/leave-guard";
import { notify } from "@/lib/notify";
import { FooterPage, PageHeader, Skeleton } from "@/shell/page";
import { pageById } from "@/shell/pages";
import { Field, NumberInput, SegmentedControl, Switch, type FieldIssue } from "@/components/ui/form-controls";
import { MarksSlider } from "@/components/ui/slider";
import { UnsavedChanges } from "@/components/ui/unsaved-changes";
import { Dialog, btn } from "@/components/ui/dialog";
import type { Acc } from "../accounts/fields";
import type { Derived } from "../accounts/types";

interface SettingsData {
  hash: string;
  accounts: { account: Acc; derived: Derived | null }[];
  profile_stats: null | Record<string, unknown>;
  globals: {
    gate_pass_rate_min: number | null; blackout_before_min: number; blackout_after_min: number;
    windows: { rth_open: string; golden_zone_from: string; all_setups_from: string; last_entry: string; flat_by: string };
    profile_risk_multiplier: Record<string, number>;
  };
  ui: { effects?: EffectsSetting };
}

const PROFILES = [
  { id: "aggressive", name: "Aggressive", pick: "base thresholds, slightly looser", use: "a fresh evaluation that wants to pass fast" },
  { id: "normal", name: "Normal", pick: "the sweep's chosen thresholds (§6.5b)", use: "every day (default)" },
  { id: "conservative", name: "Conservative", pick: "only about the top third of setups by calibrated EV", use: "after a drawdown, or a choppy week" },
  { id: "protect", name: "Protect", pick: "only about the top 10% of setups by calibrated EV", use: "a funded account close to a payout" },
  { id: "custom", name: "Custom", pick: "your own min EV and min probability", use: "anything else" },
] as const;

const SECTIONS = [
  { id: "profile", label: "Risk profile", group: "Per account" },
  { id: "thresholds", label: "Thresholds", group: "Per account" },
  { id: "funded", label: "Funded protection", group: "Per account" },
  { id: "lives", label: "Lives & λ", group: "Per account" },
  { id: "gates", label: "Gates", group: "Global" },
  { id: "windows", label: "Session & windows", group: "Global" },
  { id: "effects", label: "Effects & display", group: "Global" },
  { id: "danger", label: "Danger zone", group: "Global" },
];

const FIELDS = ["risk_profile", "custom_risk_usd", "min_ev_r", "min_target_prob", "auto_protect_within_usd", "lives_mode", "lambda_risk_penalty"] as const;

function Panel({ id, title, titleItalic, children, className }: { id: string; title: string; titleItalic: string; children: React.ReactNode; className?: string }) {
  return (
    <section id={`set-${id}`} className={cn("scroll-mt-6 rounded-md border border-line-strong/70 bg-bg-1 p-5", className)} aria-labelledby={`set-${id}-h`}>
      <h2 id={`set-${id}-h`} className="m-0 font-display text-[24px] font-normal leading-7 text-text-1">{title} <span className="italic text-cream">{titleItalic}</span></h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function LockedRow({ label, value, note = "changed through Claude Code" }: { label: string; value: React.ReactNode; note?: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-line-strong/40 py-2.5 last:border-b-0">
      <div className="flex items-center gap-2 text-[13.5px] text-text-2"><Lock className="size-3.5 text-text-3" aria-hidden="true" />{label}</div>
      <div className="text-right"><div className="font-mono text-[14px] text-text-1">{value}</div><div className="text-[11.5px] text-text-3">{note}</div></div>
    </div>
  );
}

export function SettingsPage({ onEffects }: { onEffects: (s: EffectsSetting) => void }) {
  const animated = useAnimated();
  const fx = useEffectsState();
  const [data, setData] = useState<SettingsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [edit, setEdit] = useState<Acc>({});
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const [saving, setSaving] = useState(false);
  const [active, setActive] = useState("profile");
  const [clearOpen, setClearOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await getJson<SettingsData>("/api/settings");
      setData(d); setError(null);
      setName((n) => n ?? (d.accounts.find((r) => !r.account.draft)?.account.name as string) ?? (d.accounts[0]?.account.name as string) ?? null);
      return d;
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)); return null; }
  }, []);
  useEffect(() => { load(); }, [load]);

  const row = data?.accounts.find((r) => r.account.name === name);
  const original = row?.account ?? null;
  useEffect(() => { setEdit(original ? Object.fromEntries(FIELDS.map((k) => [k, original[k]])) : {}); setIssues([]); }, [original]);
  const changes = useMemo(() => Object.fromEntries(FIELDS.filter((k) => original && JSON.stringify(edit[k] ?? null) !== JSON.stringify(original[k] ?? null)).map((k) => [k, edit[k]])), [edit, original]);
  const count = Object.keys(changes).length;
  useEffect(() => registerLeaveGuard(() => count), [count]);

  // the section list follows the scroll (scroll spy)
  useEffect(() => {
    const main = document.getElementById("cf-main");
    if (!main) return;
    const on = () => {
      let cur = SECTIONS[0].id;
      for (const s of SECTIONS) { const el = document.getElementById(`set-${s.id}`); if (el && el.getBoundingClientRect().top < 160) cur = s.id; }
      setActive(cur);
    };
    main.addEventListener("scroll", on, { passive: true });
    return () => main.removeEventListener("scroll", on);
  }, [data]);

  const save = async () => {
    if (!name || !data) return;
    setSaving(true);
    try {
      await postJson("/api/settings/account", { name, changes, hash: data.hash });
      notify({ kind: "info", title: `Saved ${name}'s settings.`, text: "They apply between trades only; an open trade keeps the size and exits it was entered with.", banner: false });
      setIssues([]); await load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) setIssues(((e.body as { issues?: FieldIssue[] }).issues) ?? [{ field: "settings", level: "error", message: e.message }]);
      else if (e instanceof ApiError && e.status === 409) { notify({ kind: "warn", title: "The accounts file changed since this page loaded it.", text: "Reloaded; make the change again." }); await load(); }
      else notify({ kind: "warn", title: "Couldn't save the settings.", text: e instanceof Error ? e.message : String(e) });
    } finally { setSaving(false); }
  };

  if (error) {
    return (
      <div className="mx-auto flex max-w-[880px] flex-col gap-6 px-6 pt-7">
        <PageHeader page={pageById("settings")} />
        <div className="flex items-start gap-3 rounded-md border border-line-strong bg-bg-1 p-5" role="alert">
          <HardDrive className="mt-0.5 size-5 text-warn" aria-hidden="true" />
          <div className="flex-1"><div className="font-semibold text-text-1">The settings can't be loaded.</div><p className="m-0 mt-1 text-[13.5px] text-text-2">{error}</p></div>
          <button type="button" className={btn.secondary} onClick={load}><RotateCw className="size-4" aria-hidden="true" />Retry</button>
        </div>
      </div>
    );
  }
  // the skeleton uses the loaded page's container, so the heading doesn't move when the data arrives (it jumped 42 px)
  if (!data) return <div className="mx-auto flex max-w-[1280px] flex-col gap-6 px-6 pt-7 min-[1440px]:px-8" aria-busy="true"><PageHeader page={pageById("settings")} /><Skeleton className="h-[420px]" /></div>;

  const a = original;
  const d = row?.derived;
  const capUsd = d?.risk_cap_usd ?? 0;
  const mult = data.globals.profile_risk_multiplier ?? {};
  const profile = String(edit.risk_profile ?? "normal");
  const w = data.globals.windows;
  const issuesOf = (k: string) => issues.filter((i) => i.field === k);
  const groups = Array.from(new Set(SECTIONS.map((s) => s.group)));

  return (
    <FooterPage>
      <div className="mx-auto flex max-w-[1280px] flex-col gap-6 px-6 pt-7 min-[1440px]:px-8">
        <PageHeader page={pageById("settings")} />
        <div className="grid grid-cols-1 gap-6 min-[1100px]:grid-cols-[200px_minmax(0,880px)]">
          {/* the sticky section list */}
          <nav aria-label="Settings sections" className="hidden h-fit flex-col gap-4 min-[1100px]:sticky min-[1100px]:top-6 min-[1100px]:flex">
            {groups.map((g) => (
              <div key={g}>
                <div className="eyebrow px-2.5 pb-1">{g}</div>
                {SECTIONS.filter((s) => s.group === g).map((s) => (
                  <a key={s.id} href={`#set-${s.id}`} onClick={(e) => { e.preventDefault(); document.getElementById(`set-${s.id}`)?.scrollIntoView({ behavior: animated ? "smooth" : "auto" }); }}
                    className={cn("block rounded-sm px-2.5 py-1.5 text-[13px]", active === s.id ? "bg-cream/10 text-cream" : "text-text-2 hover:text-text-1")}>{s.label}</a>
                ))}
              </div>
            ))}
          </nav>

          <div className="flex min-w-0 flex-col gap-5">
            <div className="flex flex-wrap items-center gap-3">
              <span className="eyebrow">Account</span>
              {data.accounts.length === 0 ? <span className="text-[13.5px] text-text-2">No accounts yet: add one on the Accounts page.</span> : (
                <SegmentedControl label="Account" value={name ?? ""} animated={animated} onChange={(v) => setName(v)}
                  options={data.accounts.map((r) => ({ value: String(r.account.name), label: <span className="font-mono">{String(r.account.name)}{r.account.draft ? <span className="ml-1.5 text-[10px] text-warn">DRAFT</span> : null}</span> }))} />
              )}
            </div>

            {a && (<>
              <Panel id="profile" title="Risk" titleItalic="profile">
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 min-[1300px]:grid-cols-3" role="radiogroup" aria-label="Risk profile">
                  {PROFILES.map((p) => {
                    const sel = profile === p.id;
                    const m = mult[p.id];
                    return (
                      <button key={p.id} type="button" role="radio" aria-checked={sel} onClick={() => setEdit((e) => ({ ...e, risk_profile: p.id, ...(p.id === "custom" && e.custom_risk_usd == null ? { custom_risk_usd: Math.floor(capUsd) } : {}) }))}
                        className={cn("flex flex-col gap-2 rounded-md border p-4 text-left transition-colors", sel ? "border-cream/60 bg-cream/[0.06]" : "border-line-strong/70 bg-bg-2 hover:border-line-strong")}>
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[15px] font-semibold text-text-1">{p.name}</span>
                          <span className="font-mono text-[12px] text-text-2">{p.id === "custom" ? "your $" : `×${m ?? "—"}`}</span>
                        </div>
                        <span className="text-[12.5px] leading-[18px] text-text-2">{p.pick}</span>
                        <span className="text-[12px] leading-4 text-text-3">{p.use}</span>
                        <dl className="m-0 mt-1 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-line-strong/50 pt-2 font-mono text-[11.5px]">
                          {["trades / week", "win rate", "avg R", "losing-day odds"].map((k) => (
                            <div key={k} className="contents"><dt className="text-text-3">{k}</dt><dd className="m-0 text-right text-text-2">n/a</dd></div>
                          ))}
                        </dl>
                      </button>
                    );
                  })}
                </div>
                <p className="m-0 mt-3 text-[12.5px] text-text-3">Backtested stats per profile come from this account's threshold sweep (step 15, models/profiles.yaml); n/a until then. Risk = {money0(Number(a.risk_usd_per_trade))} × the multiplier, never above drawdown ÷ N ({money0(capUsd)}). A profile change applies from the next trade.</p>
              </Panel>

              <Panel id="thresholds" title="Custom" titleItalic="thresholds">
                {profile !== "custom" ? (
                  <p className="m-0 text-[13.5px] text-text-2">The {PROFILES.find((p) => p.id === profile)?.name} profile takes its thresholds from the sweep (§6.5b), n/a until step 15. Pick Custom to set the risk and both thresholds yourself.</p>
                ) : (
                  <div className="flex flex-col gap-6">
                    <Field label="Risk per trade (Custom)" htmlFor="custom-risk" issues={issuesOf("custom_risk_usd")} help={`capped at drawdown ÷ N = ${money0(capUsd)}`}>
                      <div className="flex items-center gap-4 pt-5">
                        <MarksSlider label="Custom risk per trade" value={Number(edit.custom_risk_usd ?? Math.floor(capUsd))} onChange={(v) => setEdit((e) => ({ ...e, custom_risk_usd: v }))}
                          min={0} max={Math.ceil(Number(a.drawdown_usd) / 2)} step={1} cap={Math.floor(capUsd)} format={(v) => money0(v)} animated={animated} />
                        <div className="w-32 shrink-0"><NumberInput id="custom-risk" value={edit.custom_risk_usd as number | null} unit="$" onChange={(v) => setEdit((e) => ({ ...e, custom_risk_usd: v }))} invalid={issuesOf("custom_risk_usd").length > 0} /></div>
                      </div>
                    </Field>
                    <Field label="Min EV" htmlFor="min-ev" unit="R" issues={issuesOf("min_ev_r")}>
                      <div className="flex items-center gap-4 pt-1">
                        <MarksSlider label="Min EV in R" value={Number(edit.min_ev_r ?? 0.2)} onChange={(v) => setEdit((e) => ({ ...e, min_ev_r: Math.round(v * 100) / 100 }))} min={0} max={1} step={0.05} format={(v) => v.toFixed(2)} animated={animated} />
                        <div className="w-32 shrink-0"><NumberInput id="min-ev" value={edit.min_ev_r as number} unit="R" onChange={(v) => setEdit((e) => ({ ...e, min_ev_r: v }))} /></div>
                      </div>
                    </Field>
                    <Field label="Min probability of the target" htmlFor="min-p" issues={issuesOf("min_target_prob")}>
                      <div className="flex items-center gap-4 pt-1">
                        <MarksSlider label="Min target probability" value={Number(edit.min_target_prob ?? 0.4)} onChange={(v) => setEdit((e) => ({ ...e, min_target_prob: Math.round(v * 100) / 100 }))} min={0} max={1} step={0.05} format={(v) => `${Math.round(v * 100)}%`} animated={animated} />
                        <div className="w-32 shrink-0"><NumberInput id="min-p" value={edit.min_target_prob as number} unit="%" scale={100} onChange={(v) => setEdit((e) => ({ ...e, min_target_prob: v }))} /></div>
                      </div>
                    </Field>
                  </div>
                )}
              </Panel>

              <Panel id="funded" title="Funded" titleItalic="protection">
                {a.phase !== "funded" ? <p className="m-0 text-[13.5px] text-text-2">For funded accounts: {String(a.name)} is in its evaluation.</p> : (
                  <Field label="Auto-protect within" htmlFor="auto-protect" unit="$ of the payout target" issues={issuesOf("auto_protect_within_usd")} help="switches to Protect this close to the payout target, back after the payout is requested (blank = off)">
                    <div className="w-40"><NumberInput id="auto-protect" value={edit.auto_protect_within_usd as number | null} unit="$" onChange={(v) => setEdit((e) => ({ ...e, auto_protect_within_usd: v }))} /></div>
                  </Field>
                )}
              </Panel>

              <Panel id="lives" title="Lives" titleItalic="& λ">
                <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
                  <Field label="Lives mode" htmlFor="lives-mode" help="on the last life, only the best setups (§6.5b)"><Switch id="lives-mode" checked={edit.lives_mode === true} onChange={(v) => setEdit((e) => ({ ...e, lives_mode: v }))} label="Lives mode" /></Field>
                  <Field label="λ, risk penalty" htmlFor="lambda" issues={issuesOf("lambda_risk_penalty")} help="0 = pure expected R; higher penalises high-variance pairs (§6.3)">
                    <div className="w-32"><NumberInput id="lambda" value={edit.lambda_risk_penalty as number} onChange={(v) => setEdit((e) => ({ ...e, lambda_risk_penalty: v ?? 0 }))} /></div>
                  </Field>
                  <Field label="Threshold sweep" help="re-runs this account's sweep overnight (§6.8); from step 15">
                    <button type="button" disabled className={btn.secondary}><RotateCw className="size-4" aria-hidden="true" />Re-run sweep</button>
                  </Field>
                </div>
              </Panel>
            </>)}

            <Panel id="gates" title="Gates" titleItalic="(global)">
              <LockedRow label="gate_pass_rate_min" value={data.globals.gate_pass_rate_min == null ? "not set (report-only)" : `${Math.round(data.globals.gate_pass_rate_min * 100)}%`}
                note="in settings.yaml (the build reads it): changed through Claude Code" />
            </Panel>

            <Panel id="windows" title="Session" titleItalic="& windows">
              <LockedRow label="RTH open" value={`${w.rth_open} ET`} />
              <LockedRow label="Golden-zone setups from" value={`${w.golden_zone_from} ET`} />
              <LockedRow label="All setups from" value={`${w.all_setups_from} ET`} />
              <LockedRow label="Last entry" value={`${w.last_entry} ET`} />
              <LockedRow label="Flat by" value={`${w.flat_by} ET`} />
              <LockedRow label="News blackout" value={`${data.globals.blackout_before_min} min before · ${data.globals.blackout_after_min} min after`}
                note="owner's fixed rule; never below 2 (MFFU Tier-1): a change rebuilds events and labels" />
            </Panel>

            <Panel id="effects" title="Effects" titleItalic="& display">
              <div className="flex flex-col gap-3">
                <SegmentedControl label="Effects" value={fx.setting} animated={animated} onChange={(v) => onEffects(v as EffectsSetting)}
                  options={[{ value: "auto", label: <><SunMoon className="size-3.5" aria-hidden="true" />Auto</> }, { value: "full", label: <><Sparkles className="size-3.5" aria-hidden="true" />Full</> }, { value: "calm", label: <><Leaf className="size-3.5" aria-hidden="true" />Calm</> }]} />
                <p className="m-0 text-[12.5px] text-text-2">
                  Now: <span className="text-text-1">{fx.active === "full" ? "Full" : "Calm"}</span>
                  {fx.forcedBy === "reduced-motion" ? " (your system asks for reduced motion)" : fx.forcedBy === "live-engine" ? " (the live engine runs on this machine in market hours)" : fx.setting === "auto" ? " (Auto: Calm while the live engine runs here in market hours, or when the system asks for reduced motion)" : ""}.
                  Saved in the panel's settings on the SSD. Sound: off (the panel has no sounds).
                </p>
              </div>
            </Panel>

            <Panel id="danger" title="Danger" titleItalic="zone" className="border-crit/40">
              <div className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div><div className="text-[13.5px] font-medium text-text-1">Re-run this account's sweep</div><div className="text-[12.5px] text-text-3">its profiles go stale until the overnight sweep finishes (from step 15)</div></div>
                  <button type="button" disabled className={btn.secondary}><Gauge className="size-4" aria-hidden="true" />Re-run sweep</button>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line-strong/50 pt-3">
                  <div><div className="text-[13.5px] font-medium text-text-1">Clear the panel's local UI settings</div><div className="text-[12.5px] text-text-3">Effects back to Auto; this browser forgets the dock and splash state. Accounts are untouched.</div></div>
                  <button type="button" className={cn(btn.secondary, "border-crit/50 text-crit")} onClick={() => setClearOpen(true)}>Clear UI settings</button>
                </div>
              </div>
            </Panel>
          </div>
        </div>
      </div>

      <UnsavedChanges open={count > 0} count={count} saving={saving} animated={animated} onSave={save}
        onReset={() => { setEdit(original ? Object.fromEntries(FIELDS.map((k) => [k, original[k]])) : {}); setIssues([]); }}
        blocked={issues.some((i) => i.level === "error") ? "fix the marked fields first" : null} note="applies between trades only" />

      <Dialog open={clearOpen} onOpenChange={setClearOpen} title="Clear the" titleItalic="UI settings?"
        description="Effects go back to Auto, and this browser forgets the dock's minimized state and the splash and Home intro flags. Accounts and presets stay as they are."
        footer={<>
          <button type="button" className={btn.secondary} onClick={() => setClearOpen(false)}>Cancel</button>
          <button type="button" className={btn.primary} onClick={() => {
            try { ["creamflow.effects", "creamflow.dockMinimized", "creamflow.splashShown", "creamflow.homeIntroSeen", "creamflow.notes"].forEach((k) => { localStorage.removeItem(k); sessionStorage.removeItem(k); }); } catch { /* storage blocked */ }
            onEffects("auto"); setClearOpen(false);
            notify({ kind: "info", title: "UI settings cleared.", text: "Effects: Auto.", banner: false });
          }}><ShieldCheck className="size-4" aria-hidden="true" />Clear</button>
        </>} />
    </FooterPage>
  );
}
