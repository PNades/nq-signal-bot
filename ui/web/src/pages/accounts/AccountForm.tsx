// The account edit form (SPEC §9b item 1; design-system/pages/accounts.md): the §9b groups as bordered panels (max 880 px)
// beside a sticky summary of the live-as-you-type values; the preset picker fills every rule field; Save as preset; the
// raising-risk dialog with its typed confirmation; the unsaved-changes bar; delete with hold-to-confirm + the name.
// Every check and derived value comes from the server (POST /api/accounts/check, debounced), the one source of truth.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, BookmarkPlus, CircleAlert, Clock, Trash2 } from "lucide-react";
import { ApiError, postJson } from "@/lib/api";
import { money, money0 } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useAnimated } from "@/effects";
import { registerLeaveGuard } from "@/lib/leave-guard";
import { notify } from "@/lib/notify";
import { Field, IssueList, LivesPips, NumberInput, SegmentedControl, Switch, TextInput, type FieldIssue } from "@/components/ui/form-controls";
import { PresetPicker } from "@/components/ui/preset-picker";
import { UnsavedChanges } from "@/components/ui/unsaved-changes";
import { HoldToConfirm } from "@/components/ui/hold-to-confirm";
import { Dialog, btn } from "@/components/ui/dialog";
import { toast } from "@/components/ui/toast";
import { GROUPS, type Acc, type FieldDef } from "./fields";
import type { AccountsData, Preset, Report, RiskEffect } from "./types";

const ACCOUNT_OWN = new Set(["name", "active", "draft", "demo", "traderspost_webhook_env", "copied_accounts", "payouts", "first_funded_trade_date", "last_payout_date", "pending_risk", "preset", "status", "started", "ended"]);
const eq = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

function useDebounced<T>(value: T, ms: number) {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

function FieldInput({ f, acc, set, issues, animated }: { f: FieldDef; acc: Acc; set: (k: string, v: unknown) => void; issues: FieldIssue[]; animated: boolean }) {
  const v = acc[f.key];
  const invalid = issues.some((i) => i.level === "error");
  const id = `fld-${f.key}`;
  switch (f.kind) {
    case "text": return <TextInput id={id} value={String(v ?? "")} onChange={(x) => set(f.key, x === "" ? null : x)} invalid={invalid} mono={f.mono} />;
    case "time": return <TextInput id={id} value={String(v ?? "")} onChange={(x) => set(f.key, x)} invalid={invalid} mono />;
    case "date": return <TextInput id={id} value={String(v ?? "")} onChange={(x) => set(f.key, x === "" ? null : x)} invalid={invalid} mono placeholder="YYYY-MM-DD" />;
    case "money": case "number": return <NumberInput id={id} value={v as number | null} onChange={(x) => set(f.key, x)} unit={f.unit} invalid={invalid} />;
    case "int": return <NumberInput id={id} value={v as number | null} onChange={(x) => set(f.key, x == null ? null : Math.round(x))} unit={f.unit} invalid={invalid} />;
    case "pct": return <NumberInput id={id} value={v as number | null} onChange={(x) => set(f.key, x)} unit="%" scale={100} invalid={invalid} />;
    case "bool": return <Switch id={id} checked={v === true} onChange={(x) => set(f.key, x)} label={f.label} />;
    case "seg": return <SegmentedControl label={f.label} value={String(v ?? f.options![0].value)} onChange={(x) => set(f.key, x)} options={f.options!} animated={animated} />;
    case "lastlife": {
      const auto = v === "auto" || v == null;
      return (
        <div className="flex items-center gap-2">
          <SegmentedControl label={f.label} value={auto ? "auto" : "custom"} onChange={(x) => set(f.key, x === "auto" ? "auto" : 0.2)} animated={animated}
            options={[{ value: "auto", label: "Auto" }, { value: "custom", label: "Top %" }]} />
          {!auto && <div className="w-24"><NumberInput value={v as number} onChange={(x) => set(f.key, x)} unit="%" scale={100} invalid={invalid} /></div>}
        </div>
      );
    }
    default: return null;
  }
}

function DivisorField({ acc, original, set, animated }: { acc: Acc; original: Acc | null; set: (patch: Acc) => void; animated: boolean }) {
  const n = Number(acc.max_risk_fraction_divisor ?? 3);
  const was = original ? Number(original.max_risk_fraction_divisor ?? 3) : null;
  const dd = Number(acc.drawdown_usd ?? 0);
  return (
    <div className="flex flex-wrap items-center gap-3">
      <SegmentedControl label="Risk fraction" value={String(n)} animated={animated}
        onChange={(x) => set({ max_risk_fraction_divisor: Number(x), risk_usd_per_trade: Math.floor(dd / Number(x)) })}
        options={[2, 3, 4].map((k) => ({ value: String(k), label: `1/${k}`, title: `risk ${money0(Math.floor(dd / k))} per trade` }))} />
      {was !== null && n < was && <span className="flex items-center gap-1.5 text-[12.5px] text-warn"><CircleAlert className="size-3.5" aria-hidden="true" />raises risk: saving asks you to type the confirmation, then it waits for the sweep</span>}
      {was !== null && n > was && <span className="text-[12.5px] text-text-2">lowers risk: applies from the next trade</span>}
    </div>
  );
}

function TiersField({ acc, set }: { acc: Acc; set: (k: string, v: unknown) => void }) {
  const tiers = (acc.scaling_tiers as Acc[] | null) ?? [];
  const upd = (i: number, k: string, v: unknown) => set("scaling_tiers", tiers.map((t, j) => (j === i ? { ...t, [k]: v } : t)));
  return (
    <div className="flex flex-col gap-2">
      {tiers.length === 0 && <p className="m-0 text-[12.5px] text-text-3">No scaling plan: the limits above always apply.</p>}
      {tiers.length > 0 && (
        <table className="w-full border-collapse text-[12.5px]">
          <thead><tr className="text-left text-text-3"><th className="pb-1 font-normal">From profit ($, blank = any)</th><th className="pb-1 font-normal">Max minis</th><th className="pb-1 font-normal">Max micros</th><th className="pb-1 font-normal">Total</th><th /></tr></thead>
          <tbody>
            {tiers.map((t, i) => (
              <tr key={i}>
                <td className="pr-2 pb-1.5"><NumberInput value={t.min_profit_usd as number | null} onChange={(x) => upd(i, "min_profit_usd", x)} unit="$" placeholder="any" /></td>
                <td className="pr-2 pb-1.5"><NumberInput value={t.max_minis as number} onChange={(x) => upd(i, "max_minis", x ?? 0)} /></td>
                <td className="pr-2 pb-1.5"><NumberInput value={t.max_micros as number} onChange={(x) => upd(i, "max_micros", x ?? 0)} /></td>
                <td className="pr-2 pb-1.5"><NumberInput value={(t.max_contracts_total as number | null) ?? null} onChange={(x) => upd(i, "max_contracts_total", x)} /></td>
                <td className="pb-1.5"><button type="button" className={btn.ghost} onClick={() => set("scaling_tiers", tiers.filter((_, j) => j !== i).length ? tiers.filter((_, j) => j !== i) : null)} aria-label="Remove this tier"><Trash2 className="size-3.5" aria-hidden="true" /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <button type="button" className={cn(btn.secondary, "w-fit")} onClick={() => set("scaling_tiers", [...tiers, { min_profit_usd: tiers.length ? 1000 : null, max_minis: 1, max_micros: 10 }])}>Add a tier</button>
    </div>
  );
}

function AfterFirstField({ acc, set, animated }: { acc: Acc; set: (k: string, v: unknown) => void; animated: boolean }) {
  const a = acc.after_first_payout as Acc | null;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-3"><Switch checked={a != null} onChange={(on) => set("after_first_payout", on ? { profit_cap_mode: "consistency", profit_target_usd: 1000, consistency_pct: 0.5 } : null)} label="Different caps after the first payout" />
        <span className="text-[12.5px] text-text-2">{a ? "these caps replace the ones above once a payout is recorded" : "the same caps after the first payout"}</span></div>
      {a && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <SegmentedControl label="Mode after the first payout" value={String(a.profit_cap_mode)} animated={animated} onChange={(x) => set("after_first_payout", { ...a, profit_cap_mode: x })}
            options={[{ value: "consistency", label: "Consistency" }, { value: "none", label: "None" }]} />
          <NumberInput value={a.profit_target_usd as number} unit="$" onChange={(x) => set("after_first_payout", { ...a, profit_target_usd: x })} />
          <NumberInput value={a.consistency_pct as number} unit="%" scale={100} onChange={(x) => set("after_first_payout", { ...a, consistency_pct: x })} />
        </div>
      )}
    </div>
  );
}

function StopRow({ label, v, note }: { label: string; v: Record<string, number | null>; note: string }) {
  return (
    <div>
      <dt className="text-[12.5px] text-text-2">{label}</dt>
      <dd className="m-0 mt-1 grid grid-cols-2 gap-2">
        <span><span className="block font-mono text-[18px] leading-6 text-text-1">{v.passive ?? "—"} <span className="text-[12px] text-text-3">pts</span></span><span className="text-[11.5px] text-text-3">limit entry</span></span>
        <span><span className="block font-mono text-[18px] leading-6 text-text-1">{v.marketable ?? "—"} <span className="text-[12px] text-text-3">pts</span></span><span className="text-[11.5px] text-text-3">marketable / stop-limit</span></span>
      </dd>
      <dd className="m-0 mt-1 text-[12px] text-text-3">{note}</dd>
    </div>
  );
}

export function AccountForm({ data, name, onClose, reload, initialPreset = null, onAddFunded, backLabel = "All accounts" }: {
  data: AccountsData; name: string | null; onClose: () => void; reload: () => Promise<AccountsData | null>;
  /** where the back button goes: the accounts list, or Performance when opened from its breakdown */
  backLabel?: string;
  /** a new account's preset (from "Add its funded account") */ initialPreset?: string | null;
  /** a passed evaluation offers its funded account (owner 2026-11-16) */ onAddFunded?: (presetId: string) => void;
}) {
  const animated = useAnimated();
  const row = name ? data.accounts.find((r) => r.account.name === name) : undefined;
  const original: Acc | null = row ? row.account : null;
  const blank = useMemo(() => {
    const p = data.presets.find((x) => x.id === (initialPreset ?? "mffu_rapid_eod_25k_evaluation")) ?? data.presets[0];
    const fields = Object.fromEntries(Object.entries(p.fields).filter(([k]) => !ACCOUNT_OWN.has(k)));
    return { ...fields, name: "", active: true, draft: false, demo: false, traderspost_webhook_env: null, copied_accounts: 0, payouts: [], preset: p.id } as Acc;
  }, [data.presets, initialPreset]);
  const [acc, setAcc] = useState<Acc>(() => original ?? blank);
  // a passed evaluation: its funded account is a new account with its own ID, from the same plan's funded preset
  const fundedPreset = useMemo(() => {
    const own = data.presets.find((x) => x.id === acc.preset);
    return own ? data.presets.find((x) => x.label === own.label && x.stage === "funded") ?? null : null;
  }, [data.presets, acc.preset]);
  const passedEval = acc.phase === "evaluation" && acc.status === "passed";
  // saved as passed just now: ask about the funded account before closing (owner 2026-11-16)
  const [askFunded, setAskFunded] = useState(false);
  const [report, setReport] = useState<Report | null>(row ? { derived: row.derived, issues: row.issues, ok: row.ok } : null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [effect, setEffect] = useState<RiskEffect | null>(null);
  const [typed, setTyped] = useState("");
  const [presetOpen, setPresetOpen] = useState(false);
  const [presetForm, setPresetForm] = useState({ label: "", firm: "", stage: "evaluation" });
  const [presetIssues, setPresetIssues] = useState<string | null>(null);
  const [deleteTyped, setDeleteTyped] = useState("");
  const errorSummary = useRef<HTMLDivElement>(null);

  const changed = useMemo(() => {
    const base = original ?? blank;
    return Object.keys({ ...base, ...acc }).filter((k) => !eq(base[k], acc[k]));
  }, [acc, original, blank]);
  const dirty = changed.length > 0 || !original;
  useEffect(() => registerLeaveGuard(() => changed.length), [changed.length]);

  const debounced = useDebounced(acc, 250);
  useEffect(() => {
    let live = true;
    postJson<Report>("/api/accounts/check", { account: debounced, original_name: original ? original.name : null })
      .then((r) => live && setReport(r)).catch(() => { /* the last report stays; the save reports any error */ });
    return () => { live = false; };
  }, [debounced, original]);

  const set = useCallback((k: string, v: unknown) => setAcc((a) => ({ ...a, [k]: v })), []);
  const patch = useCallback((p: Acc) => setAcc((a) => ({ ...a, ...p })), []);
  const issuesOf = (key: string) => (report?.issues ?? []).filter((i) => i.field === key || i.field.startsWith(`${key}.`));
  const errors = (report?.issues ?? []).filter((i) => i.level === "error");

  const fillPreset = (id: string) => {
    const p = data.presets.find((x) => x.id === id);
    if (!p) return;
    const fields = Object.fromEntries(Object.entries(p.fields).filter(([k]) => !ACCOUNT_OWN.has(k)));
    setAcc((a) => ({ ...a, ...fields, preset: p.id }));
    notify({ kind: "info", title: `Filled from ${p.label} · ${p.stage}.`, text: "Every rule field now has the preset's value; check them and save.", banner: false });
  };

  const save = async (confirm?: string) => {
    setSaving(true); setSaveError(null);
    const account: Acc = { ...acc, draft: false };   // saving = checked by the owner (a draft becomes a real account)
    try {
      await postJson("/api/accounts/save", { account, original_name: original ? original.name : null, hash: data.hash, confirm });
      setEffect(null); setTyped("");
      notify({ kind: "info", title: `Saved ${String(account.name)}.`, text: "Written to the panel's accounts file on the SSD; the previous version is in panel/history/.", banner: false });
      const fresh = await reload();
      if (fresh && account.name !== name) history.replaceState({}, "", `/accounts/${String(account.name)}`);
      if (account.phase === "evaluation" && account.status === "passed" && original?.status !== "passed" && onAddFunded) { setAskFunded(true); return; }
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 428) { setEffect((e.body as { effect: RiskEffect }).effect); }
      else if (e instanceof ApiError && e.status === 409) { setSaveError("The file changed since this page loaded it: reloading"); await reload(); }
      else if (e instanceof ApiError && e.status === 422) {
        const issues = ((e.body as { issues?: FieldIssue[] }).issues) ?? [];
        setReport((r) => ({ derived: r?.derived ?? null, issues, ok: false }));
        setSaveError("Fix the marked fields first");
        requestAnimationFrame(() => errorSummary.current?.focus());
      } else setSaveError(e instanceof Error ? e.message : String(e));
    } finally { setSaving(false); }
  };

  const savePreset = async () => {
    setPresetIssues(null);
    try {
      await postJson("/api/presets/save", { ...presetForm, fields: acc, presets_hash: data.presets_hash });
      setPresetOpen(false);
      notify({ kind: "info", title: `Saved preset ${presetForm.label} (${presetForm.stage}).`, text: "It is in the preset picker now, marked SAVED. A new preset's stages get their profile thresholds from the next overnight sweep (from step 15).", banner: false });
      await reload();
    } catch (e) { setPresetIssues(e instanceof Error ? e.message : String(e)); }
  };

  // deleting one of the owner's own presets: a quick confirm in the picker, then ~10 s to undo (owner 2026-11-15)
  const deletePreset = async (p: { id: string; label: string; firm: string; stage: string }) => {
    const full = data.presets.find((x) => x.id === p.id);
    if (!full || full.builtin) return;
    try {
      await postJson("/api/presets/delete", { id: p.id, typed: p.label });
      await reload();
      toast(`Deleted preset ${p.label} (${p.stage}).`, {
        description: "Undo puts it back in the picker.", kind: "warn", ms: 10000,
        action: { label: "Undo", onClick: async () => {
          try {
            await postJson("/api/presets/save", { label: full.label, firm: full.firm, stage: full.stage, fields: full.fields });
            await reload();
            notify({ kind: "info", title: `Restored preset ${full.label} (${full.stage}).`, banner: false });
          } catch (e) { notify({ kind: "warn", title: "Couldn't restore the preset.", text: e instanceof Error ? e.message : String(e) }); }
        } },
      });
    } catch (e) { notify({ kind: "warn", title: "Couldn't delete the preset.", text: e instanceof Error ? e.message : String(e) }); }
  };

  const remove = async () => {
    try {
      await postJson("/api/accounts/delete", { name: original!.name, typed: deleteTyped, hash: data.hash });
      notify({ kind: "warn", title: `Deleted account ${String(original!.name)}.`, text: "The previous file is kept in panel/history/.", banner: false });
      await reload(); onClose();
    } catch (e) { setSaveError(e instanceof Error ? e.message : String(e)); }
  };

  const d = report?.derived;
  const isNew = !original;
  return (
    <div className="mx-auto flex max-w-[1280px] flex-col gap-6 px-6 pt-7 min-[1440px]:px-8" data-account-form>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button type="button" onClick={onClose} className={cn(btn.ghost, "-ml-3")}><ArrowLeft className="size-4" aria-hidden="true" />{backLabel}</button>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={btn.secondary} onClick={() => { setPresetForm({ label: "", firm: String(acc.firm ?? ""), stage: acc.phase === "funded" ? "funded" : "evaluation" }); setPresetOpen(true); }}>
            <BookmarkPlus className="size-4" aria-hidden="true" />Save as preset
          </button>
        </div>
      </div>
      <header>
        <div className="eyebrow"><span className="text-text-2">CREAMFLOW</span> / ACCOUNTS / {isNew ? "NEW" : String(original!.name).toUpperCase()}</div>
        <h1 className="mt-2 font-display text-[38px] leading-[40px] tracking-[-0.01em] text-text-1">
          {isNew ? "New" : <span className="font-mono text-[32px]">{String(original!.name)}</span>} <span className="italic text-cream">{isNew ? "account" : acc.draft ? "· draft to check" : "· account"}</span>
        </h1>
        <p className="mt-1.5 text-[16px] leading-6 text-text-2">{acc.draft ? "Pre-filled from its §6.7 preset. Check every field against the firm's site, then save it as checked." : "Every field is editable; checks run as you type."}</p>
      </header>

      {saveError && (
        <div ref={errorSummary} tabIndex={-1} className="rounded-sm border-l-2 border-crit bg-crit/10 px-4 py-3 text-[13.5px] outline-none" role="alert">
          <div className="font-semibold text-text-1">{saveError}</div>
          {errors.length > 0 && <ul className="m-0 mt-1.5 list-none p-0">{errors.map((i, k) => (
            <li key={k}><a href={`#fld-${i.field}`} className="text-crit underline underline-offset-2" onClick={(e) => { e.preventDefault(); document.querySelector<HTMLElement>(`[data-field="fld-${i.field}"] input, [data-field="fld-${i.field}"] button`)?.focus(); }}>{i.field}: {i.message}</a></li>
          ))}</ul>}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 min-[1200px]:grid-cols-[minmax(0,880px)_minmax(280px,1fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <section className="rounded-md border border-line-strong/70 bg-bg-1 p-5">
            <div className="eyebrow">Preset</div>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <PresetPicker presets={data.presets} value={(acc.preset as string) ?? null} onPick={fillPreset} onDelete={deletePreset} />
              <span className="text-[12.5px] text-text-3">fills every rule field; the name, webhook and copies stay</span>
            </div>
          </section>
          {GROUPS.map((g) => (
            <section key={g.id} className="rounded-md border border-line-strong/70 bg-bg-1 p-5" aria-labelledby={`grp-${g.id}`}>
              <h2 id={`grp-${g.id}`} className="m-0 font-display text-[24px] font-normal leading-7 text-text-1">{g.title} <span className="italic text-cream">{g.titleItalic}</span></h2>
              <div className="mt-4 grid grid-cols-1 gap-x-5 gap-y-4 sm:grid-cols-2">
                {g.fields.filter((f) => !f.when || f.when(acc)).map((f) => (
                  <Field key={f.key} label={f.label} htmlFor={`fld-${f.key}`} unit={f.kind === "money" || f.kind === "pct" ? undefined : f.unit} help={f.help} issues={issuesOf(f.key)} wide={f.wide}>
                    {f.kind === "divisor" ? <DivisorField acc={acc} original={original} set={patch} animated={animated} />
                      : f.kind === "tiers" ? <TiersField acc={acc} set={set} />
                      : f.kind === "after" ? <AfterFirstField acc={acc} set={set} animated={animated} />
                      : <FieldInput f={f} acc={acc} set={set} issues={issuesOf(f.key)} animated={animated} />}
                  </Field>
                ))}
              </div>
            </section>
          ))}
          {passedEval && (
            <section className="rounded-md border border-ok/40 bg-ok/[.06] p-5" data-add-funded>
              <h2 className="m-0 text-[16px] font-semibold text-text-1">Evaluation passed: add its funded account?</h2>
              <p className="m-0 mt-1 text-[13px] text-text-2">The funded account is a new account with its own name{fundedPreset ? `, from the ${fundedPreset.label} funded preset` : ""}. This evaluation is archived once saved as passed; it stays in All accounts and its totals.</p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button type="button" className={btn.primary} disabled={!fundedPreset || original?.status !== "passed"}
                  onClick={() => fundedPreset && onAddFunded?.(fundedPreset.id)} data-add-funded-button>Add funded account</button>
                {original?.status !== "passed" && <span className="text-[12.5px] text-text-3">Saving it as passed asks you next.</span>}
                {!fundedPreset && <span className="text-[12.5px] text-text-3">No funded preset for this plan: add the account from a preset.</span>}
              </div>
            </section>
          )}
          {original && (data.accounts.find((r) => r.account.name === original.name)?.trades ?? 0) > 0 && (
            <section className="rounded-md border border-line-strong/70 bg-bg-1 p-5" data-archive-instead>
              <h2 className="m-0 text-[16px] font-semibold text-text-1">This account has trades</h2>
              <p className="m-0 mt-1 text-[13px] text-text-2">An account with trades is never deleted: its history stays in All accounts and its totals. When it passes, fails or is closed, set its status above; it is archived (off the Accounts page; on Performance under All accounts, where Edit opens it again).</p>
            </section>
          )}
          {original && !((data.accounts.find((r) => r.account.name === original.name)?.trades ?? 0) > 0) && (
            <section className="rounded-md border border-crit/40 bg-bg-1 p-5">
              <h2 className="m-0 text-[16px] font-semibold text-text-1">Delete this account</h2>
              <p className="m-0 mt-1 text-[13px] text-text-2">It leaves the panel's accounts file; the previous version stays in panel/history/. Type <span className="font-mono text-text-1">{String(original.name)}</span>, then hold the button.</p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <div className="w-44"><TextInput value={deleteTyped} onChange={setDeleteTyped} placeholder={String(original.name)} mono /></div>
                <HoldToConfirm onConfirm={remove} disabled={deleteTyped !== original.name} animated={animated}><Trash2 className="size-4" aria-hidden="true" />Hold to delete</HoldToConfirm>
              </div>
            </section>
          )}
        </div>

        {/* the sticky summary: what the account's rules give, live as you type (server-computed) */}
        <aside className="min-[1200px]:sticky min-[1200px]:top-6 h-fit rounded-md border border-line-strong/70 bg-bg-2 p-5" aria-label="Live summary" data-summary>
          <div className="eyebrow">Live summary</div>
          {!d ? <p className="mt-3 text-[13px] text-text-3">The account doesn't parse yet: fix the marked fields.</p> : (
            <dl className="m-0 mt-3 flex flex-col gap-3.5">
              <div><dt className="text-[12.5px] text-text-2">Daily profit limit</dt><dd className="m-0 mt-0.5 font-mono text-[20px] text-text-1">{d.daily_profit_limit_usd != null ? money(d.daily_profit_limit_usd, false) : "none"}</dd>
                {d.daily_profit_limit_note && <dd className="m-0 text-[12px] text-text-3">{d.daily_profit_limit_note}</dd>}</div>
              <div><dt className="text-[12.5px] text-text-2">Risk per trade</dt><dd className="m-0 mt-0.5 font-mono text-[20px] text-text-1">{money(d.risk_usd_per_trade ?? 0, false)} <span className="text-[13px] text-text-3">{d.fraction} · cap {money(d.risk_cap_usd ?? 0, false)}</span></dd></div>
              <div><dt className="text-[12.5px] text-text-2">Lives at full drawdown</dt><dd className="m-0 mt-1 flex items-center gap-3"><span className="font-mono text-[20px] text-text-1">{d.lives}</span>{d.lives != null && <LivesPips lives={d.lives} />}</dd></div>
              <StopRow label="Smallest stop with full risk" v={d.min_stop_full_risk_pts} note={`below it the size cap (${d.size_cap_minis} mini-equivalent) binds`} />
              <StopRow label={`Largest stop for 1 ${d.one_contract}`} v={d.max_stop_one_contract_pts} note="above it the trade is skipped" />
              <div className="flex items-center gap-1.5 border-t border-line-strong/50 pt-3 text-[12px] text-text-3"><Clock className="size-3.5" aria-hidden="true" />balance, floor and lives left arrive with the engine (step 22)</div>
            </dl>
          )}
          {report && report.issues.length > 0 && <div className="mt-4 border-t border-line-strong/50 pt-3"><IssueList issues={report.issues} /></div>}
        </aside>
      </div>

      <UnsavedChanges open={dirty || acc.draft === true} count={changed.length}
        idleLabel="Draft: check every field, then save it as checked" saving={saving} error={null} animated={animated}
        blocked={errors.length ? `${errors.length} field${errors.length === 1 ? "" : "s"} to fix before saving` : null}
        saveLabel={isNew ? "Add account" : acc.draft ? "Save as checked" : "Save"} onSave={() => save()}
        // Reset: undo every change and stay; Discard: undo every change and close (a new account: nothing is created)
        onReset={() => { setAcc(original ?? blank); setSaveError(null); }}
        onDiscard={() => { setAcc(original ?? blank); setSaveError(null); onClose(); }}
        note="applies between trades only" />

      <Dialog open={effect !== null} onOpenChange={(v) => { if (!v) { setEffect(null); setTyped(""); } }} busy={saving}
        title="Raise risk to" titleItalic={effect ? `1/${effect.new_divisor} of the drawdown?` : ""}
        description="Raising risk waits for this account's overnight sweep and takes effect at the next session after it; until then it keeps trading at its current fraction."
        footer={<>
          <button type="button" className={btn.secondary} onClick={() => { setEffect(null); setTyped(""); }}>Cancel</button>
          <button type="button" className={btn.primary} disabled={!effect || typed.trim() !== effect.confirm_text || saving} onClick={() => save(typed.trim())}>Raise risk</button>
        </>}>
        {effect && (
          <div className="flex flex-col gap-4">
            <dl className="m-0 grid grid-cols-3 gap-3">
              <div className="rounded-sm border border-line-strong bg-bg-2 p-3"><dt className="eyebrow">New risk</dt><dd className="m-0 mt-1 font-mono text-[20px] text-text-1">{money0(effect.new_risk_usd)}</dd></div>
              <div className="rounded-sm border border-line-strong bg-bg-2 p-3"><dt className="eyebrow">Lives</dt><dd className="m-0 mt-1 font-mono text-[20px] text-text-1">{effect.lives_new} <span className="text-[13px] text-text-3">was {effect.lives_old}</span></dd></div>
              <div className="rounded-sm border border-crit/40 bg-bg-2 p-3"><dt className="eyebrow">Losses that fail it</dt><dd className="m-0 mt-1 font-mono text-[20px] text-crit">{effect.losses_that_fail}</dd></div>
            </dl>
            <div>
              <div className="eyebrow">What the caps leave of it (a win of {effect.rr}R)</div>
              <table className="mt-2 w-full border-collapse text-[13px]">
                <thead><tr className="text-left text-text-3"><th className="pb-1 font-normal">Stop</th><th className="pb-1 text-right font-normal">Effective risk</th><th className="pb-1 pl-4 font-normal">Limited by</th></tr></thead>
                <tbody>{effect.rows.map((r) => (
                  <tr key={r.stop_pts} className="border-t border-line-strong/50"><td className="py-1.5 font-mono">{r.stop_pts} pts</td><td className="py-1.5 text-right font-mono">{money(r.effective_risk_usd, false)}</td><td className="py-1.5 pl-4 text-text-2">{r.limited_by}</td></tr>
                ))}</tbody>
              </table>
            </div>
            <Field label={<>Type <span className="font-mono text-text-1">{effect.confirm_text}</span> to confirm</>} htmlFor="raise-typed">
              <TextInput id="raise-typed" value={typed} onChange={setTyped} mono placeholder={effect.confirm_text} />
            </Field>
          </div>
        )}
      </Dialog>

      <Dialog open={askFunded} onOpenChange={(v) => { if (!v) { setAskFunded(false); onClose(); } }} title="Evaluation passed:" titleItalic="add its funded account?"
        description={`${String(acc.name)} is saved as passed and archived (always in All accounts and its totals). Its funded account is a new account with its own name${fundedPreset ? `, filled from the ${fundedPreset.label} funded preset` : ""}.`}
        footer={<>
          <button type="button" className={btn.secondary} onClick={() => { setAskFunded(false); onClose(); }}>Not now</button>
          <button type="button" className={btn.primary} disabled={!fundedPreset} data-ask-funded-add
            onClick={() => { setAskFunded(false); if (fundedPreset) onAddFunded?.(fundedPreset.id); }}>Add funded account</button>
        </>}>
        {!fundedPreset && <p className="m-0 text-[13px] text-text-2">No funded preset for this plan: add the account with Add account and pick a preset.</p>}
      </Dialog>
      <Dialog open={presetOpen} onOpenChange={setPresetOpen} title="Save as" titleItalic="preset"
        description="Keeps this account's rule fields (not its name, webhook or copies) as a named preset in the picker, e.g. a larger Lucid Flex size. Its stages get profile thresholds from the next overnight sweep (from step 15)."
        footer={<>
          <button type="button" className={btn.secondary} onClick={() => setPresetOpen(false)}>Cancel</button>
          <button type="button" className={btn.primary} disabled={!presetForm.label.trim() || !presetForm.firm.trim()} onClick={savePreset}>Save preset</button>
        </>}>
        <div className="grid grid-cols-1 gap-4">
          <Field label="Name" htmlFor="pr-label" help="e.g. Lucid Flex 50k"><TextInput id="pr-label" value={presetForm.label} onChange={(v) => setPresetForm((p) => ({ ...p, label: v }))} /></Field>
          <Field label="Firm" htmlFor="pr-firm"><TextInput id="pr-firm" value={presetForm.firm} onChange={(v) => setPresetForm((p) => ({ ...p, firm: v }))} /></Field>
          <Field label="Stage" htmlFor="pr-stage">
            <SegmentedControl label="Stage" value={presetForm.stage} animated={animated} onChange={(v) => setPresetForm((p) => ({ ...p, stage: v }))}
              options={[{ value: "evaluation", label: "Evaluation" }, { value: "funded", label: "Funded" }, { value: "after first payout", label: "After 1st payout" }]} />
          </Field>
          {presetIssues && <p className="m-0 flex items-start gap-1.5 text-[12.5px] text-crit"><CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />{presetIssues}</p>}
        </div>
      </Dialog>
    </div>
  );
}

export type { Preset };
