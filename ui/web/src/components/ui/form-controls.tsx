"use client";
// Form controls for Accounts and Settings (design-system/pages/accounts.md, settings.md, MASTER §10 Inputs):
// - SegmentedControl: 21st.dev Segmented Control id 44919 (vamsiy78), code read 2026-11-15: its look and spring pill
//   kept; its `radix-ui` RadioGroup and a missing `segmented-control-utils/direction` file replaced by a plain
//   role="radiogroup" with roving arrow keys (no new package).
// - Switch: 21st.dev Switch id 11886 (jshguo), code read 2026-11-15: its look kept on a native button role="switch"
//   instead of @radix-ui/react-switch (no new package).
// - Field + NumberInput: our own (labels always visible, unit suffix in mono, error under the field with an icon,
//   linked by aria-describedby; number inputs use inputmode="decimal"); searched 21st.dev ("number input with unit
//   suffix"): coss Number Field 28180 needs Base UI, Unit Field's helpers weren't returned, so none fit.
// - LivesPips: from 21st.dev Progress Bar id 9659 (jatin-yadav05, segmented bar), code read: one rounded segment per
//   life, no hover wobble, no demo slider.
import { useId, type ReactNode } from "react";
import { motion } from "motion/react";
import { CircleAlert, Info, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";

export interface FieldIssue { field: string; level: "error" | "warning" | "info"; message: string }

export function Field({ label, htmlFor, unit, help, issues = [], children, className, wide = false }: {
  label: ReactNode; htmlFor?: string; unit?: string; help?: ReactNode; issues?: FieldIssue[]; children: ReactNode; className?: string; wide?: boolean;
}) {
  const id = useId();
  const err = issues.find((i) => i.level === "error");
  const warn = issues.find((i) => i.level === "warning");
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", wide && "sm:col-span-2", className)} data-field={htmlFor}>
      <label htmlFor={htmlFor} className="text-[12.5px] font-medium leading-4 text-text-2">{label}{unit && <span className="ml-1 font-mono text-[11px] text-text-3">{unit}</span>}</label>
      <div aria-describedby={err || warn || help ? id : undefined}>{children}</div>
      {err ? <p id={id} className="m-0 flex items-start gap-1.5 text-[12px] leading-4 text-crit"><CircleAlert className="mt-px size-3.5 shrink-0" aria-hidden="true" />{err.message}</p>
        : warn ? <p id={id} className="m-0 flex items-start gap-1.5 text-[12px] leading-4 text-warn"><TriangleAlert className="mt-px size-3.5 shrink-0" aria-hidden="true" />{warn.message}</p>
        : help ? <p id={id} className="m-0 text-[12px] leading-4 text-text-3">{help}</p> : null}
    </div>
  );
}

const inputBase = "h-8 w-full rounded-sm border bg-bg-2 px-2.5 text-[13.5px] text-text-1 outline-none placeholder:text-text-3 focus:border-cream";

export function TextInput({ id, value, onChange, placeholder, invalid, mono = false, disabled }: {
  id?: string; value: string; onChange: (v: string) => void; placeholder?: string; invalid?: boolean; mono?: boolean; disabled?: boolean;
}) {
  return <input id={id} value={value} placeholder={placeholder} disabled={disabled} onChange={(e) => onChange(e.target.value)} spellCheck={false} autoComplete="off"
    className={cn(inputBase, mono && "font-mono", invalid ? "border-crit" : "border-line-strong", disabled && "opacity-60")} />;
}

/** A number in its display unit (percent fields show 30 for 0.30). Empty = null. */
export function NumberInput({ id, value, onChange, unit, scale = 1, step, invalid, placeholder = "—", disabled }: {
  id?: string; value: number | null | undefined; onChange: (v: number | null) => void; unit?: string; scale?: number; step?: number; invalid?: boolean; placeholder?: string; disabled?: boolean;
}) {
  const shown = value == null ? "" : String(Math.round(value * scale * 1e6) / 1e6);
  return (
    <div className={cn("flex h-8 items-center rounded-sm border bg-bg-2 focus-within:border-cream", invalid ? "border-crit" : "border-line-strong", disabled && "opacity-60")}>
      {unit === "$" && <span className="pl-2.5 font-mono text-[12px] text-text-3">$</span>}
      <input id={id} inputMode="decimal" value={shown} placeholder={placeholder} disabled={disabled} step={step}
        onChange={(e) => {
          const t = e.target.value.replace(/[,\s$%]/g, "");
          if (t === "") return onChange(null);
          const n = Number(t);
          if (Number.isFinite(n)) onChange(n / scale);
        }}
        className="h-full min-w-0 flex-1 bg-transparent px-2 text-right font-mono text-[13.5px] text-text-1 outline-none placeholder:text-text-3" />
      {unit && unit !== "$" && <span className="pr-2.5 font-mono text-[11.5px] text-text-3">{unit}</span>}
    </div>
  );
}

export function Switch({ id, checked, onChange, label, disabled }: { id?: string; checked: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean }) {
  return (
    <button id={id} type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
      className={cn("inline-flex h-6 w-11 shrink-0 items-center rounded-full border p-0.5 transition-colors",
        checked ? "border-cream/60 bg-cream" : "border-line-strong bg-bg-3", disabled && "cursor-not-allowed opacity-50")}>
      <span className={cn("block size-[18px] rounded-full transition-transform duration-150", checked ? "translate-x-[20px] bg-[#081a1b]" : "translate-x-0 bg-text-2")} />
    </button>
  );
}

export function SegmentedControl<T extends string>({ value, onChange, options, label, animated = true, size = "default", disabled }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode; title?: string }[]; label: string; animated?: boolean; size?: "sm" | "default"; disabled?: boolean;
}) {
  const layoutId = useId();
  const idx = options.findIndex((o) => o.value === value);
  const onKey = (e: React.KeyboardEvent) => {
    const d = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const next = options[(Math.max(0, idx) + d + options.length) % options.length];
    onChange(next.value);
    (e.currentTarget.querySelector(`[data-value="${next.value}"]`) as HTMLElement | null)?.focus();
  };
  return (
    <div role="radiogroup" aria-label={label} onKeyDown={onKey} aria-disabled={disabled}
      className={cn("relative inline-flex w-fit max-w-full items-center rounded-sm bg-white/[0.06] p-0.5 shadow-[inset_0_0_0_0.5px_rgba(255,255,255,.06)]",
        size === "sm" ? "h-7 text-[12px]" : "h-8 text-[13px]", disabled && "pointer-events-none opacity-50")}>
      {options.map((o) => {
        const sel = o.value === value;
        return (
          <button key={o.value} type="button" role="radio" aria-checked={sel} tabIndex={sel ? 0 : -1} data-value={o.value} data-hint={o.title} onClick={() => onChange(o.value)}
            className={cn("relative flex h-full items-center justify-center gap-1.5 whitespace-nowrap rounded-[5px] px-3 font-medium", sel ? "text-text-1" : "text-text-2 hover:text-text-1")}>
            {sel && (
              <motion.span layoutId={layoutId} aria-hidden="true"
                className="absolute inset-0 rounded-[5px] bg-white/[0.16] shadow-[0_0_0_0.5px_rgba(0,0,0,.4),inset_0_0.5px_0_rgba(255,255,255,.12),0_1px_2px_rgba(0,0,0,.3)]"
                transition={animated ? { type: "spring", stiffness: 500, damping: 38, mass: 0.8 } : { duration: 0 }} />
            )}
            <span className="relative z-10 flex items-center gap-1.5">{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function LivesPips({ lives, of, className }: { lives: number; of?: number; className?: string }) {
  const total = Math.max(lives, of ?? lives);
  return (
    <div className={cn("flex items-center gap-1", className)} role="img" aria-label={`${lives} lives`}>
      {Array.from({ length: total }).map((_, i) => (
        <span key={i} className={cn("h-2 w-5 rounded-[3px]", i < lives ? (lives <= 1 ? "bg-warn" : "bg-cream") : "bg-white/10")} />
      ))}
    </div>
  );
}

export function IssueList({ issues }: { issues: FieldIssue[] }) {
  if (!issues.length) return null;
  const icon = { error: CircleAlert, warning: TriangleAlert, info: Info } as const;
  const tone = { error: "text-crit", warning: "text-warn", info: "text-info" } as const;
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
      {issues.map((i, k) => {
        const Icon = icon[i.level];
        return <li key={k} className="flex items-start gap-2 text-[12.5px] leading-[18px] text-text-2"><Icon className={cn("mt-0.5 size-3.5 shrink-0", tone[i.level])} aria-hidden="true" /><span>{i.message}</span></li>;
      })}
    </ul>
  );
}
