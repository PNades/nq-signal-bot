// The Effects setting (design-system/MASTER.md §8; pages/settings.md "Effects & display"): Full, Calm or Auto.
// What runs is `active`: Calm when chosen, when the OS asks for reduced motion (always wins), or, with Auto, while the
// server says the live engine runs on this machine during market hours (never before step 21).
import { createContext, useContext } from "react";

export type EffectsSetting = "full" | "calm" | "auto";
export type Effects = "full" | "calm";
export interface EffectsState {
  setting: EffectsSetting;
  active: Effects;
  /** why Calm is active when it wasn't chosen: "reduced-motion" | "live-engine" | null */
  forcedBy: "reduced-motion" | "live-engine" | null;
}
export const EffectsContext = createContext<EffectsState>({ setting: "auto", active: "full", forcedBy: null });
export const useEffectsState = () => useContext(EffectsContext);
export const useEffectsMode = () => useContext(EffectsContext).active;
export const useAnimated = () => useContext(EffectsContext).active === "full";

export function resolveEffects(setting: EffectsSetting, reducedMotion: boolean, liveEngineHere: boolean): EffectsState {
  if (reducedMotion) return { setting, active: "calm", forcedBy: "reduced-motion" };
  if (setting === "calm") return { setting, active: "calm", forcedBy: null };
  if (setting === "auto" && liveEngineHere) return { setting, active: "calm", forcedBy: "live-engine" };
  return { setting, active: "full", forcedBy: null };
}
