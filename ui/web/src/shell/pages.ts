// The panel's pages (SPEC §9b items 0-6) in dock order (design-system/pages/shell.md). Keys 1-6 open them in this
// order, skipping a page that isn't available yet (Live, from step 21): Home 1, Gameplan 2, Accounts 3, Settings 4,
// Review 5, Performance 6; they renumber when Live exists.
import { Activity, ChartNoAxesCombined, House, Map, ScanEye, SlidersHorizontal, Wallet, type LucideIcon } from "lucide-react";

export type PageId = "home" | "live" | "gameplan" | "accounts" | "settings" | "review" | "performance";

export interface PageDef {
  id: PageId;
  name: string;
  icon: LucideIcon;
  /** the page header (pages/shell.md: eyebrow, two-line title with the second line in cream italic, subtitle) */
  title: [string, string];
  subtitle: string;
  /** set while the page isn't available yet: why, shown in the dock tooltip and the palette */
  unavailable?: string;
}

export const PAGES: PageDef[] = [
  { id: "home", name: "Home", icon: House, title: ["Cream", "Flow"], subtitle: "" },
  { id: "live", name: "Live", icon: Activity, title: ["Live", "status"], subtitle: "Every account, its risk and its open trade.", unavailable: "from step 21" },
  { id: "gameplan", name: "Gameplan", icon: Map, title: ["Today's", "gameplan"], subtitle: "Upload, check and confirm the morning plan before 09:30." },
  { id: "accounts", name: "Accounts", icon: Wallet, title: ["Prop", "accounts"], subtitle: "Each account's firm rules, caps and risk, saved on the SSD." },
  { id: "settings", name: "Settings", icon: SlidersHorizontal, title: ["Strategy", "settings"], subtitle: "Risk profiles per account, the owner's fixed rules and display." },
  { id: "review", name: "Review", icon: ScanEye, title: ["Setup", "review"], subtitle: "Judge each setup before its outcome is shown." },
  { id: "performance", name: "Performance", icon: ChartNoAxesCombined, title: ["Trading", "performance"], subtitle: "Backtest, demo and prop results, guarded by the month ledger." },
];

export const pageById = (id: PageId) => PAGES.find((p) => p.id === id)!;

/** Pages in key order (1-6): the available ones. */
export const KEYED_PAGES = PAGES.filter((p) => !p.unavailable);

export function pageFromPath(pathname: string): PageId {
  const seg = pathname.replace(/^\/+/, "").split("/")[0];
  const hit = PAGES.find((p) => p.id === seg && !p.unavailable);
  return hit ? hit.id : "home";
}
export const pathOf = (id: PageId) => (id === "home" ? "/" : `/${id}`);
