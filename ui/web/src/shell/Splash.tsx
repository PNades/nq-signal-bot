// Splash / loading screen (MASTER §2, pages/shell.md "only on first load"): the logo mark and the CreamFlow name in
// Dot Matrix Text (21st.dev 25484, which stops redrawing once settled), on the first open of a browser session, until
// the server's first status arrives (at least ~1 s in Full so it reads as a moment, not a flash). Calm: no splash.
// It never covers the top bar (kill/flat) or the banners; it fades out over 220 ms and is click-through once fading.
import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { DotMatrixText } from "@/components/ui/dot-text";
import { LogoMark } from "./brand";

const SPLASH_KEY = "creamflow.splashShown";
let shownHere = false;
export function shouldShowSplash() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
  try { if (sessionStorage.getItem(SPLASH_KEY) === "1") return false; sessionStorage.setItem(SPLASH_KEY, "1"); } catch { if (shownHere) return false; }
  shownHere = true;
  return true;
}

export function Splash({ animated, ready, onDone }: { animated: boolean; ready: boolean; onDone: () => void }) {
  const [minDone, setMinDone] = useState(!animated);
  useEffect(() => { if (!animated) return; const t = setTimeout(() => setMinDone(true), 1100); return () => clearTimeout(t); }, [animated]);
  const leaving = !animated || (minDone && ready);
  useEffect(() => { if (leaving) { const t = setTimeout(onDone, animated ? 240 : 0); return () => clearTimeout(t); } }, [leaving, animated, onDone]);
  return (
    // below the top bar (52 px) and the ticker tape (28 px): KILL / FLAT ALL work from the first frame (pages/home.md)
    <motion.div className="fixed inset-x-0 bottom-0 top-[80px] z-[25] grid place-items-center bg-bg-0" aria-hidden="true" data-splash
      initial={{ opacity: 1 }} animate={{ opacity: leaving ? 0 : 1 }} transition={{ duration: animated ? 0.22 : 0 }}
      style={{ pointerEvents: leaving ? "none" : "auto" }}>
      <div className="flex flex-col items-center gap-5">
        <LogoMark size={56} />
        <div className="relative h-[84px] w-[440px]">
          <DotMatrixText text="CreamFlow" transition="fade" dotSize={3} gap={2} activeColor="#FFE0C2" fontFamily='"Geist Variable", system-ui, sans-serif' className="absolute inset-0" />
        </div>
        <div className="eyebrow">LOCAL CONTROL PANEL · 127.0.0.1</div>
      </div>
    </motion.div>
  );
}
