// A small read cache for the panel's GET answers (owner 2026-11-16): every page change preloads the next page's data here, and on
// a Performance account switch the page loads its
// own data and the open tab's data together and shows them in one step; the tab then finds its answer here at once
// instead of drawing a loading placeholder. Answers are kept briefly and only in memory (this tab of the browser).
import { getJson, onPost } from "./api";

/** a kept answer counts as fresh for this long (a tab mounting right after the page loaded it doesn't ask again) */
const FRESH_MS = 5_000;
/** answers older than this are never shown */
const KEEP_MS = 60_000;
/** at most this many answers kept (the oldest go first) */
const MAX_KEPT = 40;

const kept = new Map<string, { at: number; data: unknown }>();
const flying = new Map<string, Promise<unknown>>();
// a save can change any page's data: forget everything kept (owner 2026-11-16, page preloading)
onPost(() => kept.clear());

/** The kept answer for `url`, if there is one young enough to show. */
export function peek<T>(url: string | null): T | undefined {
  if (!url) return undefined;
  const e = kept.get(url);
  return e && Date.now() - e.at <= KEEP_MS ? (e.data as T) : undefined;
}

/** Keep `data` as the answer for `url` (e.g. the calendar's latest-month answer under its month's own address). */
export function prime(url: string, data: unknown) {
  kept.delete(url);
  kept.set(url, { at: Date.now(), data });
  while (kept.size > MAX_KEPT) kept.delete(kept.keys().next().value as string);
}

/** GET `url`: a fresh kept answer, the request already on its way, or a new request. Aborting `signal` only stops this
 * caller waiting; a shared request carries on for the others. */
export function load<T>(url: string, signal?: AbortSignal): Promise<T> {
  const e = kept.get(url);
  if (e && Date.now() - e.at <= FRESH_MS) return Promise.resolve(e.data as T);
  let p = flying.get(url) as Promise<T> | undefined;
  if (!p) {
    p = getJson<T>(url).then((d) => { prime(url, d); return d; }).finally(() => flying.delete(url));
    flying.set(url, p);
  }
  if (!signal) return p;
  return new Promise<T>((ok, fail) => {
    if (signal.aborted) { fail(new DOMException("aborted", "AbortError")); return; }
    signal.addEventListener("abort", () => fail(new DOMException("aborted", "AbortError")), { once: true });
    p!.then(ok, fail);
  });
}
