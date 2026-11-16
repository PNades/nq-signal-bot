// Page changes (owner 2026-11-16): one rule for every page change: the current page stays on screen until the next
// page's data is loaded, then the next page crossfades in, filled. `preloadPath` knows what each page needs to open
// filled; `open` (main.tsx) waits for it, then switches. Hovering a dock icon or a link to a page preloads it.
import { createContext, useContext } from "react";
import { load } from "./prefetch";
import { readFocus } from "./focus";
import { pageFromPath } from "@/shell/pages";
import { preloadPerformance } from "@/pages/performance/query";

/** each page's own data address (the pages read their first data from the cache under these) */
export const PAGE_URL = { home: "/api/home", accounts: "/api/accounts", settings: "/api/settings" } as const;

/** Load what the page at `path` needs to open filled (from the cache when fresh). */
export function preloadPath(path: string): Promise<unknown> {
  const id = pageFromPath(path);
  if (id === "home") return load(PAGE_URL.home);
  if (id === "accounts") return load(PAGE_URL.accounts);
  if (id === "settings") return load(PAGE_URL.settings);
  if (id === "performance") return preloadPerformance(readFocus());
  return Promise.resolve();
}

export const NavContext = createContext<{ open: (path: string, state?: Record<string, unknown>) => void }>({
  open: (path) => location.assign(path),
});
export const useNav = () => useContext(NavContext);
