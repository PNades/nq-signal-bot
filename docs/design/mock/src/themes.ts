// Theme comparison for the owner (step 14a part 0; preview only, ?theme=...). Each 21st.dev theme's own dark
// tokens (fetched with get_theme 2026-11-14) mapped onto CreamFlow's token names. State colours (ok/warn/crit),
// P&L and the chart palette never change. Where a theme has fewer surface steps than CreamFlow, the missing
// step is noted. Fonts named by a theme are not loaded: CreamFlow's fonts stay.
export type ThemeId = "current" | "original" | "thuray" | "amber";

export const THEMES: Record<ThemeId, { name: string; author: string; url: string; note: string; tokens: Record<string, string> }> = {
  current: {
    name: "Cocoa Amber (picked)", author: "serafimcloud", url: "https://21st.dev/community/themes/cocoa-amber",
    note: "picked by the owner 2026-11-14: own background #111111; cream #FFE0C2 is the primary; cocoa #393028 secondary; radius 0.5rem",
    tokens: {},
  },
  original: {
    name: "Original CreamFlow tokens", author: "MASTER.md draft", url: "",
    note: "the first draft: true black #0A0A0B, cream #F3E3C3 + gold #E7BF72, radii 4/6/10/14",
    tokens: {
      "--color-bg-0": "#0a0a0b", "--color-bg-1": "#111113", "--color-bg-2": "#18181b", "--color-bg-3": "#202024",
      "--color-line": "#2a2a2f", "--color-line-strong": "#3a3a41",
      "--color-text-1": "#f2ece0", "--color-text-2": "#b3ada3", "--color-text-3": "#8c867d",
      "--color-cream": "#f3e3c3", "--color-gold": "#e7bf72", "--color-caramel": "#d0945a",
      "--radius-xs": "4px", "--radius-sm": "6px", "--radius-md": "10px", "--radius-lg": "14px",
    },
  },
  thuray: {
    name: "THURAY Gold Void", author: "suliin", url: "https://21st.dev/community/themes/thuray-gold-void-1788909549433",
    note: "own background #050505; bg-2 #121212 interpolated (the theme has card #0A0A0A and secondary #1A1A1A only); radius 0",
    tokens: {
      "--color-bg-0": "#050505", "--color-bg-1": "#0a0a0a", "--color-bg-2": "#121212", "--color-bg-3": "#1a1a1a",
      "--color-line": "rgba(244,242,234,0.07)", "--color-line-strong": "rgba(244,242,234,0.12)",
      "--color-text-1": "#f4f2ea", "--color-text-2": "#8b8b8b", "--color-text-3": "#8b8b8b",
      "--color-cream": "#f4f2ea", "--color-gold": "#c9a227", "--color-caramel": "#8a6d1f",
      "--radius-xs": "0px", "--radius-sm": "0px", "--radius-md": "0px", "--radius-lg": "0px",
    },
  },
  amber: {
    name: "Amber Dark", author: "vanweerenbern", url: "https://21st.dev/community/themes/amber-dark-1788951724951",
    note: "own background #09090B; gold = its ring/sidebar amber #E3AF35 (its .dark primary is a grey, likely a slip); radius 0.375rem",
    tokens: {
      "--color-bg-0": "#09090b", "--color-bg-1": "#101013", "--color-bg-2": "#17171b", "--color-bg-3": "#1c1c21",
      "--color-line": "rgba(255,255,255,0.08)", "--color-line-strong": "rgba(255,255,255,0.14)",
      "--color-text-1": "#fafafa", "--color-text-2": "#a1a1aa", "--color-text-3": "#a1a1aa",
      "--color-cream": "#fafafa", "--color-gold": "#e3af35", "--color-caramel": "#c2820a",
      "--radius-xs": "2px", "--radius-sm": "4px", "--radius-md": "6px", "--radius-lg": "6px",
    },
  },
};

export function applyTheme(id: ThemeId) {
  const root = document.documentElement;
  for (const [k, v] of Object.entries(THEMES[id].tokens)) root.style.setProperty(k, v);
  root.dataset.theme = id;
}
