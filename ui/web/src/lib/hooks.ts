// Small shared hooks: page visibility (MASTER §8 pause rules), viewport width (the shell's narrow-screen rules),
// reduced motion, and keyboard shortcuts that never fire while typing (pages/shell.md Keyboard).
import { useEffect, useState } from "react";

export function usePageVisible() {
  const [v, setV] = useState(() => document.visibilityState === "visible");
  useEffect(() => {
    const on = () => setV(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return v;
}

export function useViewportWidth() {
  const [w, setW] = useState(() => window.innerWidth);
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return w;
}

export function useReducedMotion() {
  const q = "(prefers-reduced-motion: reduce)";
  const [r, setR] = useState(() => matchMedia(q).matches);
  useEffect(() => {
    const m = matchMedia(q);
    const on = () => setR(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return r;
}

/** True while focus is in a field where typing must not trigger shortcuts. */
export function isTyping(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  if (!t) return false;
  const tag = t.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable;
}

/** Recomputes from the system clock every second (MASTER principle 2: never counted down from a stored value). */
export function useNow(periodMs = 1000) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), periodMs);
    return () => clearInterval(id);
  }, [periodMs]);
  return now;
}
