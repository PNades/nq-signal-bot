"use client";
// KPI Tile: 21st.dev id 40086 (konsstepnov), code read 2026-11-15: no network calls, no remote assets.
// CreamFlow changes (MASTER.md §3.5, §4): good news in P&L green and bad news in P&L red, always with the turning
// arrow; the value in the mono face; the tile on the card surface with the stronger border (Cocoa Amber's own
// border is nearly invisible on cards); `animate` off = Calm (values snap, the line doesn't draw in).
// The delta badge sits beside the value in a wide tile and under it in a narrow one (container query), so a row
// of tiles never mixes the two.

import { type ClassValue, clsx } from "clsx";
import { ArrowUpRight } from "lucide-react";
import { animate, motion, useMotionValue, useTransform } from "motion/react";
import {
  type ChangeEvent, type PointerEvent, type SyntheticEvent,
  useCallback, useEffect, useId, useState, useSyncExternalStore,
} from "react";
import { twMerge } from "tailwind-merge";

function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)); }

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
const reducesMotion = () => !!window.matchMedia?.(REDUCED_MOTION).matches;
function watchMotion(change: () => void) {
  const query = window.matchMedia?.(REDUCED_MOTION);
  query?.addEventListener("change", change);
  return () => query?.removeEventListener("change", change);
}

export type KpiTileProps = {
  label: string;
  value?: number;
  format?: (value: number) => string;
  delta?: number;
  deltaLabel?: string;
  lowerIsBetter?: boolean;
  trend?: readonly number[];
  trendLabels?: readonly string[];
  live?: boolean;
  loading?: boolean;
  /** CreamFlow: false = Calm (no roll, no draw-in) */
  animated?: boolean;
  className?: string;
};

export function deltaTone(delta: number, lowerIsBetter = false) {
  const direction = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  const tone = (direction === "up") === lowerIsBetter ? "negative" : "positive";
  return { direction, tone: direction === "flat" ? "neutral" : tone } as const;
}

const TONES = {
  negative: ["text-pnl-down", "bg-pnl-down/12"],
  neutral: ["text-text-2", "bg-bg-3"],
  positive: ["text-pnl-up", "bg-pnl-up/12"],
} as const;
const TURN = { down: "rotate-90", flat: "rotate-45", up: "rotate-0" } as const;
const WORD = { down: "Down", flat: "No change", up: "Up" } as const;

const [W, H, PAD] = [100, 40, 3];
const EASE = [0.22, 1, 0.36, 1] as const;
const INSTANT = { duration: 0 } as const;
const tween = (duration: number) => ({ duration, ease: EASE });
const [ROLL, MORPH, DRAW] = [0.6, 0.5, 0.9].map(tween);
const SKELETON = "block animate-pulse rounded-sm bg-bg-3 motion-reduce:animate-none";
const VALUE = "font-mono font-medium text-[24px] text-text-1 tabular-nums leading-none tracking-tight";
const TILE = "@container flex min-w-0 flex-col gap-3 rounded-md border border-line-strong/70 bg-bg-2 p-4 text-text-1";
const LABEL = "eyebrow truncate";
const BADGE = "inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 font-mono font-medium text-[11px] tabular-nums";
const ARROW = "size-3.5 transition-transform duration-500 motion-reduce:transition-none";
const SPARK = "relative h-10 rounded-sm has-[:focus-visible]:outline-hidden has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-cream";
const CAPTION = "-mt-1.5 min-h-4 truncate text-text-3 text-[12px]";
const PING = "absolute inset-0 animate-ping rounded-full bg-current motion-reduce:hidden";
const DOT = "pointer-events-none absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-current motion-reduce:transition-none";
const decimal = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

type Point = { label: string; value: number; x: number; y: number };

function placePoints(trend: readonly number[], labels?: readonly string[]) {
  const kept = trend.flatMap((value, index) => (Number.isFinite(value) ? [{ label: labels?.[index] ?? "", value }] : []));
  const min = Math.min(...kept.map((point) => point.value));
  const span = Math.max(...kept.map((point) => point.value)) - min;
  const step = (W - PAD * 2) / Math.max(kept.length - 1, 1);
  return kept.map(({ label, value }, index): Point => ({
    label: label || `${index + 1} of ${kept.length}`, value, x: PAD + index * step,
    y: span ? H - PAD - ((value - min) / span) * (H - PAD * 2) : H / 2,
  }));
}

const spot = (point: Point) => ({ left: `${(point.x / W) * 100}%`, top: `${(point.y / H) * 100}%` });

export function KpiTile({
  label, value, format = decimal.format, delta, deltaLabel, lowerIsBetter = false, trend = [], trendLabels,
  live = false, loading = false, animated = true, className,
}: KpiTileProps) {
  const systemReduced = useSyncExternalStore(watchMotion, reducesMotion, () => false);
  const reduced = systemReduced || !animated;
  const id = useId();
  const [active, setActive] = useState(null as number | null);
  const points = placePoints(trend, trendLabels);
  const last = points.length - 1;
  if (active !== null && (loading || last < 1)) setActive(null);
  const at = active === null || last < 1 ? null : Math.min(active, last);
  const point = at === null ? undefined : points[at];
  const end = points[last];
  const isEmpty = value === undefined || !Number.isFinite(value);
  const hasDelta = delta !== undefined && Number.isFinite(delta) && !isEmpty;
  const { direction, tone } = deltaTone(hasDelta ? delta : 0, lowerIsBetter);
  const [toneText, toneTint] = TONES[tone];

  const target = isEmpty ? 0 : value;
  const shown = useMotionValue(reduced ? target : 0);
  const text = useTransform(() => {
    const now = format(shown.get());
    return point ? format(point.value) : isEmpty ? "—" : now;
  });
  useEffect(() => {
    const roll = animate(shown, target, reduced ? INSTANT : ROLL);
    return () => roll.stop();
  }, [reduced, shown, target]);

  const pick = useCallback((event: PointerEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - box.left) / Math.max(box.width, 1)) * W;
    const index = Math.round(((x - PAD) / (W - PAD * 2)) * last);
    setActive(last < 1 ? null : Math.min(Math.max(index, 0), last));
  }, [last]);
  const letGo = useCallback((event: SyntheticEvent) => {
    const other = event.type === "blur" ? ":hover" : ":focus-within";
    const tile = event.currentTarget.closest("article");
    return tile?.matches(other) || setActive(null);
  }, []);
  const onRange = useCallback((event: ChangeEvent<HTMLInputElement>) => setActive(Number(event.target.value)), []);
  const onFocus = useCallback(() => setActive((now) => now ?? last), [last]);
  useEffect(() => {
    const dismiss = ({ key }: globalThis.KeyboardEvent) => key === "Escape" && setActive(null);
    document.addEventListener("keydown", dismiss);
    return () => document.removeEventListener("keydown", dismiss);
  }, []);

  const line = points.map((p, i) => `${i ? "L" : "M"}${p.x} ${p.y}`).join(" ");
  const area = end ? `${line} L${end.x} ${H} L${PAD} ${H} Z` : "";
  const read = point ?? end;

  return (
    <article aria-busy={loading || undefined} aria-labelledby={`${id}-label`} className={cn(TILE, className)} data-slot="kpi-tile" onPointerLeave={letGo}>
      <p className={LABEL} id={`${id}-label`}>{label}</p>
      {loading ? (
        ["h-7 w-28", "h-3 w-20", "h-10"].map((size) => <span className={cn(SKELETON, size)} key={size} />)
      ) : (
        <>
          <div className="flex flex-col items-start gap-2.5 @[260px]:flex-row @[260px]:flex-wrap @[260px]:items-baseline @[260px]:gap-x-2.5">
            <motion.span aria-hidden="true" className={cn(VALUE, isEmpty && !point && "text-text-3")}>{text}</motion.span>
            {isEmpty ? null : <span className="sr-only">{format(value)}</span>}
            {hasDelta ? (
              <span className={cn(BADGE, toneTint, toneText)} data-direction={direction} data-tone={tone}>
                <span className="sr-only">{`${WORD[direction]} `}</span>
                <ArrowUpRight aria-hidden="true" className={cn(ARROW, TURN[direction])} />
                {`${delta > 0 ? "+" : delta < 0 ? "−" : ""}${Math.abs(delta).toFixed(1)}%`}
              </span>
            ) : null}
          </div>
          <p className={CAPTION}>{point ? point.label : isEmpty ? "No data yet" : deltaLabel}</p>
          <div className={cn(SPARK, hasDelta ? toneText : "text-gold")}>
            <svg aria-hidden="true" className="block size-full touch-pan-y overflow-visible" onPointerDown={pick} onPointerMove={pick} preserveAspectRatio="none" viewBox={`0 0 ${W} ${H}`}>
              <defs>
                <linearGradient id={`${id}-fill`} x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0" stopColor="currentColor" stopOpacity={0.2} />
                  <stop offset="1" stopColor="currentColor" stopOpacity={0} />
                </linearGradient>
                <clipPath id={`${id}-clip`}>
                  <motion.rect animate={{ width: W + PAD * 2 }} height={H + PAD * 2} initial={{ width: reduced ? W + PAD * 2 : 0 }} key={String(reduced)} transition={reduced ? INSTANT : DRAW} x={-PAD} y={-PAD} />
                </clipPath>
              </defs>
              {points.length < 2 ? (
                <path className="stroke-current opacity-50 [stroke-dasharray:3_4]" d={`M${PAD} ${H / 2} H${W - PAD}`} vectorEffect="non-scaling-stroke" />
              ) : (
                <g clipPath={`url(#${id}-clip)`}>
                  <motion.path animate={{ d: area }} fill={`url(#${id}-fill)`} initial={false} key={`area-${points.length}-${reduced}`} transition={reduced ? INSTANT : MORPH} />
                  <motion.path animate={{ d: line }} fill="none" initial={false} key={`line-${points.length}-${reduced}`} stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} transition={reduced ? INSTANT : MORPH} vectorEffect="non-scaling-stroke" />
                </g>
              )}
            </svg>
            {live && points.length > 1 && end ? (
              <span className={cn(DOT, "transition-[left,top] duration-500")} style={spot(end)}><span className={PING} /></span>
            ) : null}
            {point ? <span className={cn(DOT, "size-2.5 border-2 border-bg-2")} style={spot(point)} /> : null}
            {points.length > 1 && read ? (
              <input aria-label={`${label} trend`} aria-valuetext={`${read.label}: ${format(read.value)}`} className="sr-only" max={last} onBlur={letGo} onChange={onRange} onFocus={onFocus} type="range" value={at ?? last} />
            ) : null}
          </div>
        </>
      )}
    </article>
  );
}

export default KpiTile;
