import type { BjorkTone } from "./liquid-text-core-palette";
// CreamFlow is dark only, so the tone is always "dark".
export function useBjorkTone(tone?: BjorkTone): BjorkTone { return tone ?? "dark"; }
