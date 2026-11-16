"use client";
// Floating Dock: 21st.dev id 18014 (Aceternity, manuarora700), code read 2026-11-15: no network calls; the demo's remote
// logo image is not used. CreamFlow changes: Lucide instead of tabler icons, glass on the Cocoa surfaces, the
// active page marked with a cream dot and label, `animated` off = Calm (no magnification), the mobile menu dropped
// (the panel targets 960 px and up). `trailing` (owner 2026-11-15): an element after the items, behind a hairline
// (the minimize circle). Magnification up to 1.2× only (owner 2026-11-15); the hover labels draw above the
// unsaved-changes bar (the dock's layer sits over the bar's, and the bar keeps clear of the label zone).
// Smooth sweep (owner 2026-11-15): every icon keeps a fixed 40-px slot (nothing reflows); the magnification is a
// `scale` transform from the bottom edge, driven by a critically damped spring (GPU-friendly, no width/height
// animation); each icon's centre is measured once when the pointer enters the dock, not on every move; pointer moves
// feed one motion value, which motion applies at most once per frame.
import { cn } from "@/lib/utils";
import { AnimatePresence, type MotionValue, motion, useMotionValue, useSpring, useTransform } from "motion/react";
import { useLayoutEffect, useRef, useState } from "react";

export type DockItem = { id: string; title: string; icon: React.ReactNode; disabled?: boolean };

const SLOT = 40, MAX_SCALE = 1.2, REACH = 150;
const SPRING = { stiffness: 380, damping: 32, mass: 0.5 };

export function FloatingDock({ items, active, onPick, animated = true, className, trailing }: {
  items: DockItem[]; active?: string; onPick?: (id: string) => void; animated?: boolean; className?: string; trailing?: React.ReactNode;
}) {
  const mouseX = useMotionValue(Infinity);
  // bumped when the pointer enters: each icon re-measures its centre then (the dock may have moved or morphed)
  const [epoch, setEpoch] = useState(0);
  return (
    <motion.nav
      aria-label="Pages"
      onPointerEnter={() => setEpoch((e) => e + 1)}
      onPointerMove={(e) => animated && mouseX.set(e.clientX)}
      onPointerLeave={() => mouseX.set(Infinity)}
      className={cn("mx-auto flex h-16 items-end gap-3 rounded-lg border border-white/[.08] bg-bg-2/70 px-3 pb-2.5 shadow-[0_18px_40px_rgba(0,0,0,.5)] backdrop-blur-md", className)}
    >
      {items.map((item) => (
        <IconContainer mouseX={mouseX} epoch={epoch} key={item.id} item={item} active={item.id === active} onPick={onPick} />
      ))}
      {trailing && <span className="mb-1 h-8 w-px self-end bg-white/10" aria-hidden="true" />}
      {trailing}
    </motion.nav>
  );
}

function IconContainer({ mouseX, epoch, item, active, onPick }: { mouseX: MotionValue<number>; epoch: number; item: DockItem; active: boolean; onPick?: (id: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const centre = useRef(-1e6);
  useLayoutEffect(() => {
    const r = ref.current?.getBoundingClientRect();
    if (r) centre.current = r.left + r.width / 2;
  }, [epoch]);
  const distance = useTransform(mouseX, (x) => x - centre.current);
  const scaleT = useTransform(distance, [-REACH, 0, REACH], [1, MAX_SCALE, 1]);
  const scale = useSpring(scaleT, SPRING);
  // the label rides on top of the grown icon: lifted by the growth of its height (it grows from the bottom edge)
  const labelY = useTransform(scale, (s) => -(s - 1) * SLOT);
  const [hovered, setHovered] = useState(false);
  return (
    <div ref={ref} className="relative size-10 shrink-0" onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <AnimatePresence>
        {hovered && (
          <motion.div style={{ y: labelY }} className="pointer-events-none absolute -top-8 left-1/2 -translate-x-1/2">
            <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 2 }} transition={{ duration: 0.14 }}
              className="w-fit whitespace-pre rounded-xs border border-line-strong bg-bg-3 px-2 py-0.5 text-[12px] text-text-1">
              {item.title}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
      <motion.button
        type="button"
        disabled={item.disabled}
        aria-label={item.title}
        aria-current={active ? "page" : undefined}
        // CreamFlow: hovering the icon preloads its page (main.tsx, owner 2026-11-16)
        data-dock-item={item.disabled ? undefined : item.id}
        onClick={() => onPick?.(item.id)}
        style={{ scale, transformOrigin: "50% 100%" }}
        className={cn(
          "relative flex size-10 items-center justify-center rounded-full border will-change-transform",
          active ? "border-gold/50 bg-gold/15 text-gold" : "border-white/10 bg-bg-3 text-text-2 hover:text-text-1",
          item.disabled && "opacity-40",
        )}
      >
        <span className="flex size-[18px] items-center justify-center [&>svg]:size-full">{item.icon}</span>
        {active && <span className="absolute -bottom-1.5 size-1 rounded-full bg-cream" aria-hidden="true" />}
      </motion.button>
    </div>
  );
}
