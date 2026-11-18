// Every message the panel shows (banners and toasts) goes through here, so the bell's history can show any of them
// after it has gone (owner 2026-11-15). Banner rules (owner 2026-11-15):
//   - info: fades out by itself after BANNER_INFO_MS, paused while the pointer is over it; "Got it" closes it at once
//   - warn / crit: never fade by themselves; they stay until dismissed
// Server conditions (SSD missing, lost server) are raised once per occurrence: dismissing one hides it until the
// condition clears and comes back. The history lives in this browser tab's sessionStorage (a reload keeps it).
import { useSyncExternalStore } from "react";

export type NoteKind = "info" | "warn" | "crit";
export interface Note {
  id: string;
  kind: NoteKind;
  title: string;
  text: string;
  /** ISO time it was raised */
  at: string;
  /** where it came from: "action", "server", "panel" */
  source: string;
  /** shown as a banner (false = a toast only) */
  banner: boolean;
  dismissed: boolean;
  read: boolean;
}

/** design-system/pages/shell.md (owner 2026-11-15): informational banners fade out after about 5 s */
export const BANNER_INFO_MS = 5000;
const HISTORY_MAX = 100;
const KEY = "creamflow.notes";

function load(): Note[] {
  try { const v = sessionStorage.getItem(KEY); if (v) return (JSON.parse(v) as Note[]).map((n) => ({ ...n, dismissed: true })); } catch { /* storage blocked */ }
  return [];
}
let notes: Note[] = load();
const listeners = new Set<() => void>();
function save() { try { sessionStorage.setItem(KEY, JSON.stringify(notes.slice(-HISTORY_MAX))); } catch { /* per-tab convenience only */ } }
function emit() { save(); listeners.forEach((l) => l()); }
let seq = 0;

/** a "·" separator never starts a line (owner 2026-11-18): the space before it doesn't break */
export const keepSeps = (s: string) => s.replace(/ ·(?= )/g, "\u00a0·");

export function notify(n: { kind: NoteKind; title: string; text?: string; source?: string; banner?: boolean; id?: string }): string {
  const id = n.id ?? `n${Date.now().toString(36)}${(seq++).toString(36)}`;
  notes = [...notes.filter((x) => x.id !== id), {
    id, kind: n.kind, title: keepSeps(n.title), text: keepSeps(n.text ?? ""), at: new Date().toISOString(), source: n.source ?? "panel",
    banner: n.banner ?? true, dismissed: false, read: false,
  }].slice(-HISTORY_MAX);
  emit();
  return id;
}
export function dismiss(id: string) { notes = notes.map((n) => (n.id === id ? { ...n, dismissed: true } : n)); emit(); }
export function markAllRead() { notes = notes.map((n) => ({ ...n, read: true })); emit(); }
export function markRead(id: string) { notes = notes.map((n) => (n.id === id ? { ...n, read: true } : n)); emit(); }

/** A server condition: raise it when it starts (once), drop the "active" mark when it clears. */
const activeConditions = new Set<string>();
export function condition(key: string, active: boolean, n: { kind: NoteKind; title: string; text: string }) {
  if (active && !activeConditions.has(key)) { activeConditions.add(key); notify({ ...n, source: "server", id: `${key}-${Date.now().toString(36)}` }); }
  else if (!active && activeConditions.has(key)) {
    activeConditions.delete(key);
    notes = notes.map((x) => (x.id.startsWith(`${key}-`) ? { ...x, dismissed: true } : x));
    emit();
  }
}

export function useNotes(): Note[] {
  return useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => notes);
}
