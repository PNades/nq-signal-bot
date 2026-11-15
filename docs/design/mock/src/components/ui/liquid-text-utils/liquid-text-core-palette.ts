// Tones for Liquid Text, mapped to CreamFlow tokens (MASTER §3). Dark only: the panel has no light theme.
export type BjorkTone = "dark" | "light";
export const BJORK_PALETTE: Record<BjorkTone, { text: string; background: string }> = {
  dark: { text: "#F3E3C3", background: "#0A0A0B" },
  light: { text: "#F3E3C3", background: "#0A0A0B" },
};
