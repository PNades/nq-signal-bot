// The scroll spy for a page's table of contents (owner 2026-11-16; Settings now, any page with one later): the active
// entry is the last section whose top has passed TOP_PX, except
// - a clicked entry is active at once and stays active while the page scrolls to it (until the scroll ends);
// - at the bottom of the page (it can't scroll further) the last section on screen is the active one, so the final
//   sections are reachable without padding the page.
import { useEffect, useRef } from "react";

const TOP_PX = 160;          // a section is current once its top is this close to the top of the view
const SETTLE_MS = 1200;      // a click's hold ends after this even if no scroll happens (already in place)

export function useScrollSpy(ids: string[], onActive: (id: string) => void, deps: unknown[] = []) {
  const held = useRef<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const cb = useRef(onActive);
  cb.current = onActive;

  const current = () => {
    const main = document.getElementById("cf-main");
    if (!main) return ids[0];
    const view = main.getBoundingClientRect();
    const els = ids.map((id) => document.getElementById(id)).filter((e): e is HTMLElement => !!e);
    if (main.scrollTop + main.clientHeight >= main.scrollHeight - 2) {
      // at the bottom: the last section on screen
      const onScreen = els.filter((e) => { const r = e.getBoundingClientRect(); return r.top < view.bottom - 24 && r.bottom > view.top; });
      if (onScreen.length) return onScreen[onScreen.length - 1].id;
    }
    let cur = ids[0];
    for (const e of els) if (e.getBoundingClientRect().top - view.top < TOP_PX) cur = e.id;
    return cur;
  };

  useEffect(() => {
    const main = document.getElementById("cf-main");
    if (!main) return;
    const on = () => { if (!held.current) cb.current(current()); };
    const end = () => { if (held.current) { held.current = null; window.clearTimeout(timer.current); } };
    main.addEventListener("scroll", on, { passive: true });
    main.addEventListener("scrollend", end);
    on();
    return () => { main.removeEventListener("scroll", on); main.removeEventListener("scrollend", end); window.clearTimeout(timer.current); };
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    /** go to a section: it is the active one at once, and stays so while the page scrolls there */
    go(id: string, smooth: boolean) {
      held.current = id;
      cb.current(id);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => { held.current = null; }, SETTLE_MS);
      document.getElementById(id)?.scrollIntoView({ behavior: smooth ? "smooth" : "auto" });
    },
  };
}
