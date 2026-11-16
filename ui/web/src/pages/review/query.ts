// The Review page's data addresses, shared by the page and its preload (owner 2026-11-16: a page opens filled).
import { load } from "@/lib/prefetch";
import type { Card, ReviewIndex } from "./types";

export const reviewUrl = (round?: string | null) => `/api/review${round ? `?round=${encodeURIComponent(round)}` : ""}`;
export const cardUrl = (round: string, card: string) => `/api/review/card?round=${encodeURIComponent(round)}&card=${encodeURIComponent(card)}`;

/** the round and card the page shows, kept while the panel is open (leaving and coming back keeps the place) */
export const reviewMemory: { round: string | null; card: string | null } = { round: null, card: null };

/** the card to open: the remembered one, else the first not answered yet, else the first */
export function pickCard(idx: ReviewIndex): string | null {
  const r = idx.round;
  if (!r || !r.cards.length) return null;
  if (reviewMemory.round === r.id && reviewMemory.card && r.cards.some((c) => c.id === reviewMemory.card)) return reviewMemory.card;
  return (r.cards.find((c) => !c.answered) ?? r.cards[0]).id;
}

export async function preloadReview(): Promise<void> {
  const idx = await load<ReviewIndex>(reviewUrl(reviewMemory.round));
  const id = pickCard(idx);
  if (idx.round && id) await load<Card>(cardUrl(idx.round.id, id));
}

const DRAFT_KEY = (round: string, card: string) => `creamflow.review.draft.${round}.${card}`;
export interface Draft { verdict: "take" | "skip" | null; reason: string; entry_choice: string | null; stop: string; target: string; confidence: number | null }
export const EMPTY_DRAFT: Draft = { verdict: null, reason: "", entry_choice: null, stop: "", target: "", confidence: null };

/** the reason box keeps its draft if the owner moves to another card or page (pages/review.md); this browser only */
export function readDraft(round: string, card: string): Draft {
  try {
    const raw = localStorage.getItem(DRAFT_KEY(round, card));
    return raw ? { ...EMPTY_DRAFT, ...(JSON.parse(raw) as Partial<Draft>) } : EMPTY_DRAFT;
  } catch { return EMPTY_DRAFT; }
}
export function writeDraft(round: string, card: string, d: Draft | null) {
  try {
    if (!d || (!d.verdict && !d.reason && !d.entry_choice && !d.stop && !d.target && !d.confidence)) localStorage.removeItem(DRAFT_KEY(round, card));
    else localStorage.setItem(DRAFT_KEY(round, card), JSON.stringify(d));
  } catch { /* private window or blocked storage: the draft lives only while the page is open */ }
}
