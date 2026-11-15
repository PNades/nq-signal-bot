// The kill / flat controls in the top bar (MASTER §10, SPEC §9b "Risk first", pages/shell.md): always visible, on every
// page, never glass, tilt, glow or animated in, never covered. FLAT ALL acts on one click; KILL asks once (focus on
// KILL, so Enter confirms; Esc or Cancel backs out). Both post to the same endpoints as the no-JavaScript /emergency
// page (one action path, src/nqbot/ui/actions.py). What they do is SPEC §9c's `!flat` / `!kill`, word for word.
import { useState } from "react";
import { Activity, Ban, DoorOpen, ListX, LogOut, OctagonX, Square } from "lucide-react";
import { postJson } from "@/lib/api";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";

export type RiskAction = "flat" | "kill" | "pause" | "resume";
export interface ActionResult { ok: boolean; action: RiskAction; message: string; engine: string; at_utc: string }

export const ACTION_TEXT: Record<RiskAction, string> = {
  kill: "Sends exit to every account and ticker, then blocks new entries until the next session. The engine keeps running so the exits can retry; its status reads KILLED.",
  flat: "Sends exit to every account and ticker (this also removes pending entries), and pauses new entries until Resume.",
  pause: "Stops new entries and removes pending unfilled entries; open trades are untouched.",
  resume: "Allows new entries again.",
};

export async function sendRiskAction(action: RiskAction): Promise<ActionResult> {
  try {
    return await postJson<ActionResult>(`/api/actions/${action}`);
  } catch (e) {
    return { ok: false, action, engine: "unreachable", at_utc: new Date().toISOString(),
      message: `The panel server didn't take it (${e instanceof Error ? e.message : String(e)}). Use the emergency page, nqbot kill / nqbot flat, or Discord !kill / !flat.` };
  }
}

export function RiskControls({ onResult, killOpen, setKillOpen, engineHere, accounts }: {
  onResult: (r: ActionResult) => void; killOpen: boolean; setKillOpen: (v: boolean) => void; engineHere: boolean;
  /** live counts for the KILL dialog: the saved (non-draft) accounts an exit goes to */
  accounts: number;
}) {
  const [busy, setBusy] = useState<RiskAction | null>(null);
  const run = async (a: RiskAction) => {
    setBusy(a);
    const r = await sendRiskAction(a);
    setBusy(null);
    onResult(r);
  };
  return (
    // ml-2.5: KILL and FLAT ALL read as their own group, apart from the bell, Ctrl K and Effects (owner 2026-11-15)
    <div className="ml-2.5 flex shrink-0 items-center gap-2" data-risk-controls>
      <button type="button" onClick={() => setKillOpen(true)} disabled={busy !== null} aria-haspopup="dialog"
        title={`KILL (asks once; Enter confirms): ${ACTION_TEXT.kill}`}
        className="flex h-9 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-sm border border-crit/70 bg-transparent px-3 text-[13px] font-semibold tracking-[0.04em] text-crit hover:bg-crit/10">
        <OctagonX className="size-4" strokeWidth={2} aria-hidden="true" /> KILL
      </button>
      <button type="button" onClick={() => run("flat")} disabled={busy !== null} data-flat-all
        title={`FLAT ALL (one click): ${ACTION_TEXT.flat} If the panel fails, use /emergency.`}
        className="flex h-9 shrink-0 items-center gap-2 whitespace-nowrap rounded-sm bg-crit-fill px-4 text-[13px] font-semibold tracking-[0.04em] text-white shadow-[0_0_0_1px_rgba(255,255,255,.08)_inset] hover:bg-[#e02a31]">
        <Square className="size-3.5 fill-white" strokeWidth={2} aria-hidden="true" /> {busy === "flat" ? "SENDING…" : "FLAT ALL"}
      </button>
      <ConfirmDialog open={killOpen} onOpenChange={setKillOpen} onConfirm={() => run("kill")}
        icon={<OctagonX />} eyebrow="Risk · same as Discord !kill" title="Kill trading" titleItalic="for this session?"
        description="KILL acts on every account at once."
        items={[
          // live counts from the engine (step 22); "—" while no engine runs here
          { icon: <LogOut />, label: <>Sends <code className="font-mono text-[12.5px]">exit</code> to every account and ticker</>,
            count: engineHere ? String(accounts) : "—" },
          { icon: <DoorOpen />, label: "Closes every open position", count: engineHere ? "0" : "—" },
          { icon: <ListX />, label: "Removes every pending entry", count: engineHere ? "0" : "—" },
          { icon: <Ban />, label: "Blocks new entries until the next session" },
          { icon: <Activity />, label: <>Keeps the engine running so the exits can retry; its status reads <span className="font-mono">KILLED</span></> },
        ]}
        note={engineHere ? undefined : <>No live engine runs on this machine yet (it comes at step 22), so pressing KILL now sends nothing and the counts show “—”; the request is logged.</>}
        confirmLabel={<><OctagonX className="size-4" aria-hidden="true" /> KILL</>} />
    </div>
  );
}
