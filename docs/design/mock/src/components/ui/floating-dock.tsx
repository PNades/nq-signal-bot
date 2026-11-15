"use client";
// Floating Dock: 21st.dev id 18014 (Aceternity, manuarora700), code read 2026-11-15: no network calls; the demo's remote
// logo image is not used. CreamFlow changes: Lucide instead of tabler icons, glass on the Cocoa surfaces, the
// active page marked with a cream dot and label, `animated` off = Calm (no magnification), the mobile menu dropped
// (the panel targets 960 px and up). `trailing` (owner 2026-11-15): an element after the items, behind a hairline
// (the minimize circle).
import { cn } from "@/lib/utils";
import { AnimatePresence, type MotionValue, motion, useMotionValue, useSpring, useTransform } from "motion/react";
import { useRef, useState } from "react";

export type DockItem = { id: string; title: string; icon: React.ReactNode; disabled?: boolean };

export function FloatingDock({ items, active, onPick, animated = true, className, trailing }: {
  items: DockItem[]; active?: string; onPick?: (id: string) => void; animated?: boolean; className?: string; trailing?: React.ReactNode;
}) {
  const mouseX = useMotionValue(Infinity);
  return (
    <motion.nav
      aria-label="Pages"
      onMouseMove={(e) => animated && mouseX.set(e.pageX)}
      onMouseLeave={() => mouseX.set(Infinity)}
      className={cn("mx-auto flex h-16 items-end gap-3 rounded-lg border border-white/[.08] bg-bg-2/70 px-3 pb-2.5 shadow-[0_18px_40px_rgba(0,0,0,.5)] backdrop-blur-md", className)}
    >
      {items.map((item) => (
        <IconContainer mouseX={mouseX} key={item.id} item={item} active={item.id === active} onPick={onPick} />
      ))}
      {trailing && <span className="mb-1 h-8 w-px self-end bg-white/10" aria-hidden="true" />}
      {trailing}
    </motion.nav>
  );
}

function IconContainer({ mouseX, item, active, onPick }: { mouseX: MotionValue<number>; item: DockItem; active: boolean; onPick?: (id: string) => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  const distance = useTransform(mouseX, (val) => {
    const bounds = ref.current?.getBoundingClientRect() ?? { x: 0, width: 0 };
    return val - bounds.x - bounds.width / 2;
  });
  const sizeT = useTransform(distance, [-150, 0, 150], [40, 68, 40]);
  const iconT = useTransform(distance, [-150, 0, 150], [18, 30, 18]);
  const size = useSpring(sizeT, { mass: 0.1, stiffness: 150, damping: 12 });
  const iconSize = useSpring(iconT, { mass: 0.1, stiffness: 150, damping: 12 });
  const [hovered, setHovered] = useState(false);
  return (
    <motion.button
      ref={ref}
      type="button"
      disabled={item.disabled}
      aria-label={item.title}
      aria-current={active ? "page" : undefined}
      onClick={() => onPick?.(item.id)}
      style={{ width: size, height: size }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={cn(
        "relative flex aspect-square items-center justify-center rounded-full border",
        active ? "border-gold/50 bg-gold/15 text-gold" : "border-white/10 bg-bg-3 text-text-2 hover:text-text-1",
        item.disabled && "opacity-40",
      )}
    >
      <AnimatePresence>
        {hovered && (
          <motion.div initial={{ opacity: 0, y: 10, x: "-50%" }} animate={{ opacity: 1, y: 0, x: "-50%" }} exit={{ opacity: 0, y: 2, x: "-50%" }}
            className="absolute -top-8 left-1/2 w-fit whitespace-pre rounded-xs border border-line-strong bg-bg-3 px-2 py-0.5 text-[12px] text-text-1">
            {item.title}
          </motion.div>
        )}
      </AnimatePresence>
      <motion.div style={{ width: iconSize, height: iconSize }} className="flex items-center justify-center [&>svg]:size-full">{item.icon}</motion.div>
      {active && <span className="absolute -bottom-1.5 size-1 rounded-full bg-cream" aria-hidden="true" />}
    </motion.button>
  );
}
