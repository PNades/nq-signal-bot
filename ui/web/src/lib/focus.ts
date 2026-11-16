// The panel's focus account (owner 2026-11-15): the account the top-bar switcher points at. Pages that filter by account
// follow it (Performance from stage 3). It is a viewing choice only: it never changes which accounts trade. Kept per
// browser (localStorage), a per-viewer convenience.
import { createContext, useContext } from "react";

const KEY = "creamflow.focusAccount";

export function readFocus(): string | null {
  try { return localStorage.getItem(KEY); } catch { return null; }
}
export function writeFocus(name: string | null) {
  try { if (name) localStorage.setItem(KEY, name); else localStorage.removeItem(KEY); } catch { /* storage blocked */ }
}

export interface FocusState { focus: string | null; setFocus: (name: string) => void }
export const FocusContext = createContext<FocusState>({ focus: null, setFocus: () => {} });
export const useFocusAccount = () => useContext(FocusContext);
