// Joining a card's two halves (SPEC §8): `post` (sent only after the answer) replaces everything from the decision
// minute on (its first bar is that minute whole, replacing the partial one) and brings the full lists of touches and
// King switches. Plus the small lookups the chart and the radial share.
import type { Card, DevVa, Half, Level, MapName, MapSnap, RoleRow, Zone } from "@/pages/review/types";

export function joined(card: Card): Half {
  const pre = card.pre, post = card.post;
  if (!post) return pre;
  const m = card.t.minute;
  const levels = (k: "NQ" | "ES"): Level[] => {
    const out = new Map<string, Level>();
    for (const l of pre.levels[k]) out.set(l.uid, { ...l, segs: l.segs.filter((s) => s[0] < m) });
    for (const l of post.levels[k]) {
      const had = out.get(l.uid);
      out.set(l.uid, had ? { ...l, segs: [...had.segs, ...l.segs] } : l);
    }
    return [...out.values()];
  };
  const zones = (): Zone[] => {
    const out = new Map<string, Zone>();
    for (const z of pre.zones) out.set(z.id, { ...z, pts: z.pts.filter((p) => p[0] < m) });
    for (const z of post.zones) {
      const had = out.get(z.id);
      out.set(z.id, had ? { ...z, pts: [...had.pts, ...z.pts] } : z);
    }
    return [...out.values()];
  };
  const devVa = (): DevVa | undefined => {
    if (!pre.dev_va && !post.dev_va) return undefined;
    const one = (k: keyof DevVa) => [...(pre.dev_va?.[k] ?? []).filter((s) => s[0] < m), ...(post.dev_va?.[k] ?? [])];
    return { vah: one("vah"), val: one("val") };
  };
  const maps = {} as Record<MapName, MapSnap[]>;
  for (const k of Object.keys(pre.maps) as MapName[]) maps[k] = [...pre.maps[k].filter((s) => s.t < m), ...(post.maps[k] ?? [])];
  return {
    bars: { NQ: [...pre.bars.NQ.filter((b) => b[0] < m), ...post.bars.NQ], ES: [...pre.bars.ES.filter((b) => b[0] < m), ...post.bars.ES] },
    levels: { NQ: levels("NQ"), ES: levels("ES") },
    zones: zones(),
    dev_va: devVa(),
    events: post.events,
    maps,
    kings: post.kings,
  };
}

/** the role row in force at t (rows are in time order) */
export function roleAt(rows: RoleRow[], t: number): RoleRow | null {
  let best: RoleRow | null = null;
  for (const r of rows) { if (r[0] <= t) best = r; else break; }
  return best;
}

/** the last item with `.t` (or [0]) at or before t, by binary search */
export function lastAt<T>(items: T[], t: number, key: (x: T) => number): T | null {
  let lo = 0, hi = items.length - 1, best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (key(items[mid]) <= t) { best = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return best >= 0 ? items[best] : null;
}

/** the band of a level at t (its segment covering t) */
export function segAt(l: Level, t: number): [number, number] | null {
  const s = lastAt(l.segs, t, (x) => x[0]);
  return s && t < s[1] ? [s[2], s[3]] : null;
}

export const MAP_ROOT: Record<MapName, "NQ" | "ES"> = { QQQ: "NQ", NDXP: "NQ", SPY: "ES", SPX: "ES" };

/** "+62.0M" / "−13.1M" / "+840K" (Skylit's GEX value, with its sign: Pika +, Barney −) */
export function gexText(v: number): string {
  const a = Math.abs(v), s = v < 0 ? "−" : "+";
  if (a >= 1e9) return `${s}${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${s}${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${s}${(a / 1e3).toFixed(0)}K`;
  return `${s}${a.toFixed(0)}`;
}

/** "12:40" in New York from a UTC second (the card's ET offset applied) */
export function clockOf(t: number, off: number): string {
  const d = new Date((t + off) * 1000);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}
