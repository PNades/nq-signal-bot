// The banner stack under the ticker tape (pages/shell.md; owner 2026-11-15). Newest first.
// - Info banners (e.g. "FLAT ALL: nothing was sent", a successful flat) slide down with a short fade and fade out by
//   themselves after BANNER_INFO_MS; the countdown pauses while the pointer is over the banner; "Got it" closes it at once.
// - Warning banners slide in the same way but never fade by themselves: they stay until dismissed.
// - Critical banners appear at once with no motion (MASTER §7: risk alerts never animate in) and stay until dismissed.
// Leaving, any banner fades, then its height folds (260 ms, ease-out) so what is below glides up; Calm: at once.
// Everything stays in the bell's history after it has gone (lib/notify.ts).
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Banner } from "@/components/ui/banner";
import { BANNER_INFO_MS, dismiss, useNotes, type Note } from "@/lib/notify";
import { etClock } from "@/lib/format";

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

function BannerRow({ n, animated }: { n: Note; animated: boolean }) {
  const [hover, setHover] = useState(false);
  const moves = animated && n.kind !== "crit";
  const label = n.kind === "crit" ? "Acknowledge" : "Got it";
  return (
    <motion.div className="overflow-hidden"
      initial={moves ? { opacity: 0, y: -10 } : false}
      animate={{ opacity: 1, y: 0, transition: { duration: moves ? 0.22 : 0, ease: [0, 0, 0.2, 1] } }}
      // leaving (faded out after ~5 s, Got it, Acknowledge): it fades first, then its height folds away so the banners
      // and the page below glide up instead of jumping (owner 2026-11-15; any kind, critical too: this is not an
      // entrance). Calm: gone at once.
      exit={animated
        ? { opacity: 0, height: 0, transition: { opacity: { duration: 0.16, ease: [0.4, 0, 1, 1] }, height: { duration: 0.26, delay: 0.14, ease: [0, 0, 0.2, 1] } } }
        : { opacity: 0, height: 0, transition: { duration: 0 } }}
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

export function Banners({ animated }: { animated: boolean }) {
  const shown = useNotes().filter((n) => n.banner && !n.dismissed).slice().reverse();
  return (
    <div className="relative z-[70] shrink-0 overflow-hidden" aria-live="polite" data-banners>
      <AnimatePresence initial={false}>
        {shown.map((n) => <BannerRow key={n.id} n={n} animated={animated} />)}
      </AnimatePresence>
    </div>
  );
}
