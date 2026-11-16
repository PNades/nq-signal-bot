// The banner stack under the ticker tape (pages/shell.md; owner 2026-11-15). Newest first.
// - Info banners (e.g. "FLAT ALL: nothing was sent", a successful flat) slide down with a short fade and fade out by
//   themselves after BANNER_INFO_MS; the countdown pauses while the pointer is over the banner; "Got it" closes it at once.
// - Warning banners slide in the same way but never fade by themselves: they stay until dismissed.
// - Critical banners appear at once with no motion (MASTER §7: risk alerts never animate in) and stay until dismissed.
// Leaving, any banner fades, then its height folds (260 ms, ease-out) so what is below glides up; Calm: at once.
// Everything stays in the bell's history after it has gone (lib/notify.ts).
import { useEffect, useRef, useState, useLayoutEffect } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Banner } from "@/components/ui/banner";
import { BANNER_INFO_MS, dismiss, useNotes, type Note } from "@/lib/notify";
import { etClock } from "@/lib/format";
import { GLIDE_S, glideEase } from "@/lib/glide";

function InfoTimer({ id, paused }: { id: string; paused: boolean }) {
  const left = useRef(BANNER_INFO_MS);
  const started = useRef(Date.now());
  useEffect(() => {
    if (paused) return;
    started.current = Date.now();
    const t = setTimeout(() => dismiss(id), left.current);
    return () => { clearTimeout(t); left.current = Math.max(0, left.current - (Date.now() - started.current)); };
  }, [id, paused]);
  return null;
}

function BannerRow({ n, animated, fold }: { n: Note; animated: boolean; fold: boolean }) {
  const [hover, setHover] = useState(false);
  const moves = animated && n.kind !== "crit";
  const label = n.kind === "crit" ? "Acknowledge" : "Got it";
  // on Home (fold false) a banner unfolds and folds on the glide's timing, while the scene glides alongside it from
  // the same frame (owner 2026-11-15); elsewhere it slides in at full height and folds away after fading
  const home = !fold;
  return (
    <motion.div className="overflow-hidden" data-banner-id={n.id}
      initial={moves ? (home ? { opacity: 0, height: 0 } : { opacity: 0, y: -10 }) : false}
      animate={home
        ? { opacity: 1, height: "auto", transition: { duration: moves ? GLIDE_S : 0, ease: glideEase } }
        : { opacity: 1, y: 0, transition: { duration: moves ? 0.22 : 0, ease: [0, 0, 0.2, 1] } }}
      // leaving (faded out after ~5 s, Got it, Acknowledge): it fades first, then its height folds away so the banners
      // and the page below glide up instead of jumping (owner 2026-11-15; any kind, critical too: this is not an
      // entrance). Calm: gone at once.
      // on Home (fold false) the height goes in one step after the fade: the scene then glides once (FLIP) instead of
      // re-laying out on every frame of a fold (owner 2026-11-15)
      exit={!animated ? { opacity: 0, height: 0, transition: { duration: 0 } }
        : home ? { opacity: 0, height: 0, transition: { duration: GLIDE_S, ease: glideEase } }
        : { opacity: 0, height: 0, transition: { opacity: { duration: 0.16, ease: [0.4, 0, 1, 1] }, height: { duration: 0.26, delay: 0.14, ease: [0, 0, 0.2, 1] } } }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)} onFocus={() => setHover(true)} onBlur={() => setHover(false)}
      data-banner={n.kind}>
      {n.kind === "info" && <InfoTimer id={n.id} paused={hover} />}
      <Banner kind={n.kind} title={n.title}
        actions={
          <>
            <span className="hidden font-mono text-[11px] text-text-3 min-[1100px]:inline">{etClock(new Date(n.at))} ET</span>
            {n.kind === "crit" && n.source === "server" && (
              <a href="/emergency" className="h-7 rounded-sm bg-crit-fill px-2.5 text-[12px] font-semibold leading-7 text-white">Emergency page</a>
            )}
            <button type="button" onClick={() => dismiss(n.id)} className="h-7 rounded-sm border border-line-strong bg-bg-2 px-2.5 text-[12px] text-text-1 hover:border-text-3">{label}</button>
          </>
        }>
        {n.text}
      </Banner>
    </motion.div>
  );
}

// The banners' target height: the full height of the banners now shown (leaving ones excluded), reported in the very
// commit that changes the list, before anything is painted. Home sizes its scene from it, so its glide starts in the
// same frame as the banner's own animation (owner 2026-11-15).
let target = 0;
const listeners = new Set<(px: number) => void>();
export const bannerTarget = () => target;
// the heights of a one-line and a two-line banner at the current width, measured from two hidden probe banners (Home
// works out its layout for 0 to 3 stacked banners ahead of time from them)
let probeHeights: [number, number] = [0, 0];
export const bannerHeights = () => probeHeights;
export function onBannerTarget(fn: (px: number) => void) { listeners.add(fn); return () => { listeners.delete(fn); }; }

export function Banners({ animated, fold = true }: { animated: boolean; fold?: boolean }) {
  const shown = useNotes().filter((n) => n.banner && !n.dismissed).slice().reverse();
  const ids = shown.map((n) => n.id).join(",");
  const box = useRef<HTMLDivElement>(null);
  const probe = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = probe.current; if (!el) return;
    const read = () => { const [a, b] = el.children as unknown as HTMLElement[]; probeHeights = [a.offsetHeight, b.offsetHeight]; };
    const ro = new ResizeObserver(read);
    ro.observe(el); read();
    return () => ro.disconnect();
  }, []);
  useLayoutEffect(() => {
    const want = new Set(ids ? ids.split(",") : []);
    let px = 0;
    // each banner's own height (its row may be folded to 0 while it unfolds; the banner inside is at full height)
    box.current?.querySelectorAll<HTMLElement>("[data-banner-id]").forEach((row) => {
      if (want.has(row.dataset.bannerId!)) px += (row.querySelector("[data-kind]") as HTMLElement | null)?.offsetHeight ?? 0;
    });
    if (px !== target) { target = px; listeners.forEach((fn) => fn(px)); }
  }, [ids]);
  return (
    <div ref={box} className="relative z-[70] shrink-0 overflow-hidden" aria-live="polite" data-banners>
      {/* two hidden banners (one line, two lines), only measured */}
      <div ref={probe} aria-hidden="true" className="pointer-events-none invisible absolute inset-x-0 top-0" data-banner-probe>
        {[false, true].map((two) => (
          <Banner key={String(two)} kind="info" title="Banner"
            actions={<><span className="hidden font-mono text-[11px] min-[1100px]:inline">00:00:00 ET</span><span className="h-7 rounded-sm border border-line-strong px-2.5 text-[12px] leading-7">Got it</span></>}>
            {two ? <>one<br />two</> : "one"}
          </Banner>
        ))}
      </div>
      <AnimatePresence initial={false}>
        {shown.map((n) => <BannerRow key={n.id} n={n} animated={animated} fold={fold} />)}
      </AnimatePresence>
    </div>
  );
}
