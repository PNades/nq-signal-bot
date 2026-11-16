// Accounts (SPEC §9b item 1, design-system/pages/accounts.md): the account cards (3 across at 1440 px, 2 at 1024),
// then an account's edit form at /accounts/<name> (/accounts/new for a new one). Data: GET /api/accounts (the panel's
// accounts file on the SSD, every account with its derived values and checks, every preset).
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useAnimated } from "@/effects";
import { HardDrive, Plus, RotateCw } from "lucide-react";
import { getJson, ApiError } from "@/lib/api";
import { peek, prime } from "@/lib/prefetch";
import { PAGE_URL, useNav } from "@/lib/nav";
import { pageFromPath } from "@/shell/pages";
import { FooterPage, PageHeader, Skeleton } from "@/shell/page";
import { pageById } from "@/shell/pages";
import { btn } from "@/components/ui/dialog";
import { AccountCard } from "./AccountCard";
import { AccountForm } from "./AccountForm";
import type { AccountsData } from "./types";

function subPath(): string | null {
  const seg = location.pathname.replace(/^\/+/, "").split("/");
  return seg[0] === "accounts" && seg[1] ? decodeURIComponent(seg[1]) : null;
}

// list <-> editor (owner 2026-11-15): a short fade-and-slide, forwards when an account opens (the editor comes in from
// the right), reversed when it closes, saves or discards (the list comes back from the left); instant in Calm
const SLIDE_PX = 16;
function Swap({ k, dir, children }: { k: string; dir: number; children: ReactNode }) {
  const animated = useAnimated();
  return (
    // the static wrapper clips the slide, so the page is never wider than the window during it (owner 2026-11-15)
    <div className="min-h-full overflow-x-clip">
    <AnimatePresence mode="wait" initial={false} custom={dir}>
      <motion.div key={k} custom={dir}
        variants={{
          enter: (d: number) => (animated ? { opacity: 0, x: d * SLIDE_PX } : { opacity: 1, x: 0 }),
          center: { opacity: 1, x: 0, transition: { duration: animated ? 0.22 : 0, ease: [0, 0, 0.2, 1] } },
          exit: (d: number) => (animated ? { opacity: 0, x: -d * SLIDE_PX, transition: { duration: 0.15, ease: [0.4, 0, 1, 1] } } : { opacity: 0, transition: { duration: 0 } }),
        }}
        initial="enter" animate="center" exit="exit" className="min-h-full" data-accounts-view={k}>
        {children}
      </motion.div>
    </AnimatePresence>
    </div>
  );
}

export function AccountsPage() {
  // the page opens filled: its data was loaded before the page change (lib/nav.ts)
  const [data, setData] = useState<AccountsData | null>(() => peek<AccountsData>(PAGE_URL.accounts) ?? null);
  const nav = useNav();
  // "Add its funded account" from a passed evaluation: the new account starts from the plan's funded preset
  const [newPreset, setNewPreset] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sub, setSub] = useState<string | null>(subPath);
  // +1 = opening an account (forwards), -1 = back to the list
  const dir = useRef(1);
  const prevSub = useRef(sub);
  if (prevSub.current !== sub) { dir.current = sub ? 1 : -1; prevSub.current = sub; }
  const load = useCallback(async () => {
    try { const d = await getJson<AccountsData>(PAGE_URL.accounts); prime(PAGE_URL.accounts, d); setData(d); setError(null); return d; }
    catch (e) { setError(e instanceof ApiError ? e.message : String(e)); return null; }
  }, []);
  useEffect(() => { if (!peek(PAGE_URL.accounts)) load(); }, [load]);
  // only this page's own paths: leaving for another page keeps this view until that page has come in
  useEffect(() => { const on = () => { if (pageFromPath(location.pathname) === "accounts") setSub(subPath()); }; window.addEventListener("popstate", on); return () => window.removeEventListener("popstate", on); }, []);
  const go = (next: string | null) => { history.pushState({}, "", next ? `/accounts/${encodeURIComponent(next)}` : "/accounts"); setSub(next); document.getElementById("cf-main")?.scrollTo({ top: 0 }); };
  // an editor opened from Performance's breakdown (Edit) goes back there when it closes (owner 2026-11-16)
  const back = (history.state as { back?: string } | null)?.back ?? null;
  const close = () => { if (back) nav.open(back); else go(null); };

  if (error) {
    return (
      <div className="mx-auto flex max-w-[880px] flex-col gap-6 px-6 pt-7">
        <PageHeader page={pageById("accounts")} />
        <div className="flex items-start gap-3 rounded-md border border-line-strong bg-bg-1 p-5" role="alert">
          <HardDrive className="mt-0.5 size-5 text-warn" aria-hidden="true" />
          <div className="flex-1"><div className="font-semibold text-text-1">The accounts can't be loaded.</div><p className="m-0 mt-1 text-[13.5px] text-text-2">{error}</p></div>
          <button type="button" className={btn.secondary} onClick={load}><RotateCw className="size-4" aria-hidden="true" />Retry</button>
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="mx-auto flex max-w-[1280px] flex-col gap-6 px-6 pt-7 min-[1440px]:px-8" aria-busy="true">
        <PageHeader page={pageById("accounts")} />
        <div className="grid grid-cols-1 gap-3 min-[1000px]:grid-cols-2 min-[1400px]:grid-cols-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-[300px]" />)}
        </div>
      </div>
    );
  }
  if (sub) {
    return <Swap k={`edit-${sub}`} dir={dir.current}><FooterPage><AccountForm key={sub} data={data} name={sub === "new" ? null : sub} onClose={sub === "new" ? () => go(null) : close} backLabel={back && sub !== "new" ? "Performance" : "All accounts"} reload={load}
      initialPreset={sub === "new" ? newPreset : null} onAddFunded={(id) => { setNewPreset(id); go("new"); }} /></FooterPage></Swap>;
  }
  const drafts = data.accounts.filter((r) => r.account.draft).length;
  // a finished account (passed, failed, closed) is archived: this page lists active accounts only; past ones are reached
  // from Performance → All accounts → Edit (owner 2026-11-16)
  const isArchived = (r: { account: Record<string, unknown> }) => r.account.status === "passed" || r.account.status === "failed" || r.account.status === "closed";
  const archived = data.accounts.filter(isArchived).length;
  const shown = data.accounts.filter((r) => !isArchived(r));
  return (
    <Swap k="list" dir={dir.current}>
    <FooterPage>
      <div className="mx-auto flex max-w-[1280px] flex-col gap-6 px-6 pt-7 min-[1440px]:px-8">
        <PageHeader page={pageById("accounts")} right={
          <button type="button" className={btn.primary} onClick={() => { setNewPreset(null); go("new"); }}><Plus className="size-4" aria-hidden="true" />Add account</button>
        }>
          <p className="m-0 mt-2 font-mono text-[12px] text-text-3">{data.accounts.length - archived} account{data.accounts.length - archived === 1 ? "" : "s"}{archived ? ` · ${archived} archived (on Performance, All accounts)` : ""}{drafts ? ` · ${drafts} draft${drafts === 1 ? "" : "s"} to check` : ""} · {data.folder}</p>
        </PageHeader>
        {shown.length === 0 ? (
          <div className="rounded-md border border-line-strong/60 bg-bg-1 p-8 text-center text-[14px] text-text-2">{archived ? "No active accounts. Add one from a preset; past accounts are on Performance (All accounts)." : "No accounts yet. Add one from a preset."}</div>
        ) : (
          <div className="grid grid-cols-1 gap-3 min-[1000px]:grid-cols-2 min-[1400px]:grid-cols-3">
            {shown.map((r) => <AccountCard key={String(r.account.name)} row={r} presets={data.presets} onOpen={() => go(String(r.account.name))} />)}
          </div>
        )}
      </div>
    </FooterPage>
    </Swap>
  );
}
