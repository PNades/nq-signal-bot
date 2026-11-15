"use client";
// Delta Number Ticker: 21st.dev id 44850 (dhxwhy), code read 2026-11-15: no network calls, no animation library.
// CreamFlow changes: duration 0 = Calm (no roll, chip fades only); the label as an eyebrow, the chip in the mono face,
// the tooltip on the popover surface; unused data-* attributes and a rolling flag dropped. Chip colours come from the
// shadcn aliases in styles.css (chart-2 = P&L green, destructive = P&L red), always with arrow and sign (MASTER §3.5).

import * as React from "react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";

type DivProps = React.ComponentProps<"div">;
export type DeltaDirection = "up" | "down";
export type DeltaGoodDirection = "up" | "down" | "neutral";
export type DeltaTrend = "good" | "bad" | "neutral";
export type DeltaNumberFormat = Intl.NumberFormatOptions | ((value: number) => string);

export type DeltaNumberTickerProps = DivProps & {
  value: number; previousValue?: number; format?: DeltaNumberFormat; formatDelta?: (delta: number) => string; locale?: string; label?: string;
  goodDirection?: DeltaGoodDirection; showAbsolute?: boolean; showPercent?: boolean; percentDigits?: number; ghost?: boolean; ghostDuration?: number;
  tooltip?: boolean; changedAt?: string; duration?: number; stagger?: number; valueClassName?: string; onSettle?: (value: number) => void;
};

type Group = "lead" | "int" | "frac" | "tail";
type Cell = { key: string; char: string; kind: "digit" | "glyph"; group: Group; order: number };
type Layout = { text: string; cells: Cell[]; byKey: { [key: string]: Cell }; digits: number; intDigits: number };
type Formatter = { text: (value: number) => string; parts: ((value: number) => Intl.NumberFormatPart[]) | null };
type ChipState = { id: number; basis: number; abs: string | null; pct: string | null; trend: DeltaTrend; direction: DeltaDirection; from: string; leaving: boolean; animate: boolean };
type GhostState = { id: number; text: string; out: boolean };

const EASE = "cubic-bezier(0.22, 1, 0.36, 1)";
const EASE_IN = "cubic-bezier(0.4, 0, 1, 1)";
const EASE_FLIGHT = "cubic-bezier(0.65, 0, 0.35, 1)";
const STRIP = [..."01234567890123456789"];
const TONE: { [trend in DeltaTrend]: string } = {
  good: "bg-chart-2/15 text-[color:color-mix(in_oklab,var(--color-chart-2)_75%,var(--color-foreground))]",
  bad: "bg-destructive/15 text-[color:color-mix(in_oklab,var(--color-destructive)_75%,var(--color-foreground))]",
  neutral: "bg-muted text-foreground",
};
const MASK = "linear-gradient(to bottom, transparent, black 6px, black calc(100% - 6px), transparent)";
const isDigit = (char: string) => char >= "0" && char <= "9";

function useReducedMotion() {
  const [reduced, setReduced] = React.useState(true);
  React.useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return reduced;
}

function createFormatter(format: DeltaNumberFormat | undefined, locale: string): Formatter {
  if (typeof format === "function") return { text: format, parts: null };
  const intl = new Intl.NumberFormat(locale, format);
  return { text: (value) => intl.format(value), parts: (value) => intl.formatToParts(value) };
}

function createDeltaFormatter(format: DeltaNumberFormat | undefined, locale: string): (delta: number) => string {
  const source = typeof format === "function" || !format ? {} : format;
  const intl = new Intl.NumberFormat(locale, {
    minimumFractionDigits: source.minimumFractionDigits, maximumFractionDigits: source.maximumFractionDigits, useGrouping: source.useGrouping,
    notation: source.notation, compactDisplay: source.compactDisplay, signDisplay: "exceptZero",
  });
  return (delta) => intl.format(delta);
}

function splitParts(parts: Intl.NumberFormatPart[]) {
  let lead = "", integer = "", fraction = "", tail = "", stage = 0;
  for (const part of parts) {
    if ((part.type === "integer" || part.type === "group") && stage <= 1) { stage = 1; integer += part.value; continue; }
    if ((part.type === "decimal" || part.type === "fraction") && stage >= 1 && stage <= 2) { stage = 2; fraction += part.value; continue; }
    if (stage === 0) lead += part.value;
    else { stage = 3; tail += part.value; }
  }
  return { lead, integer, fraction, tail };
}

function splitText(text: string) {
  let first = -1, last = -1;
  for (let index = 0; index < text.length; index++) { if (isDigit(text[index] ?? "")) { if (first < 0) first = index; last = index; } }
  if (first < 0) return { lead: text, integer: "", fraction: "", tail: "" };
  const body = text.slice(first, last + 1);
  const dot = body.lastIndexOf(".");
  const hasFraction = dot > 0 && [...body.slice(dot + 1)].every(isDigit);
  return { lead: text.slice(0, first), integer: hasFraction ? body.slice(0, dot) : body, fraction: hasFraction ? body.slice(dot) : "", tail: text.slice(last + 1) };
}

function buildLayout(value: number, formatter: Formatter): Layout {
  const text = formatter.text(value);
  const { lead, integer, fraction, tail } = formatter.parts ? splitParts(formatter.parts(value)) : splitText(text);
  const cells: Cell[] = [];
  const push = (group: Group, chars: string, keyOf: (index: number, length: number) => string) => {
    const list = [...chars];
    list.forEach((char, index) => cells.push({ key: keyOf(index, list.length), char, kind: isDigit(char) ? "digit" : "glyph", group, order: 0 }));
  };
  push("lead", lead, (index) => `l${index}`);
  push("int", integer, (index, length) => `i${length - 1 - index}`);
  push("frac", fraction, (index) => `f${index}`);
  push("tail", tail, (index) => `t${index}`);
  let order = 0;
  for (let index = cells.length - 1; index >= 0; index--) { const cell = cells[index]; if (cell && cell.kind === "digit") { cell.order = order; order += 1; } }
  const byKey: { [key: string]: Cell } = {};
  for (const cell of cells) byKey[cell.key] = cell;
  return { text, cells, byKey, digits: order, intDigits: [...integer].filter(isDigit).length };
}

export function rollRange(from: number, to: number, up: boolean): [number, number] {
  let start = from;
  let target = up ? to : to + 10;
  if (up) { while (target <= start) target += 10; } else { while (target >= start) target -= 10; }
  if (target > 19) { target -= 10; start -= 10; }
  if (target < 0) { target += 10; start += 10; }
  return [start, target];
}

function trendOf(delta: number, goodDirection: DeltaGoodDirection): DeltaTrend {
  if (delta === 0 || goodDirection === "neutral") return "neutral";
  return (goodDirection === "up") === delta > 0 ? "good" : "bad";
}

const indexOf = (key: string) => Number(key.slice(1));

type CellViewProps = { cell: Cell; direction: DeltaDirection | "none"; duration: number; stagger: number; reduced: boolean; grow: boolean; leaving: boolean; onGone: (key: string) => void };

function CellView({ cell, direction, duration, stagger, reduced, grow, leaving, onGone }: CellViewProps) {
  const nodeRef = React.useRef(null as HTMLSpanElement | null);
  const stripRef = React.useRef(null as HTMLSpanElement | null);
  const digit = cell.kind === "digit";
  const shown = React.useRef(grow && digit ? 0 : Number(cell.char));
  const delay = cell.order * stagger;

  React.useLayoutEffect(() => {
    const node = nodeRef.current;
    if (!grow || !node || reduced || typeof node.animate !== "function") return;
    const width = node.getBoundingClientRect().width;
    const animation = node.animate([{ width: "0px", opacity: 0 }, { width: `${width}px`, opacity: 1 }], { duration, delay, easing: EASE, fill: "backwards" });
    return () => animation.cancel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useLayoutEffect(() => {
    if (!digit) return;
    const strip = stripRef.current;
    const to = Number(cell.char);
    let from = shown.current;
    shown.current = to;
    if (!strip) return;
    const running = strip.getAnimations().filter((animation) => animation.playState === "running");
    if (running.length) {
      const matrix = /matrix\(([^)]+)\)/.exec(getComputedStyle(strip).transform);
      const ty = matrix ? Number(matrix[1]?.split(",")[5]) : Number.NaN;
      const glyph = strip.offsetHeight / STRIP.length;
      if (Number.isFinite(ty) && glyph > 0) from = Math.min(19, Math.max(0, -ty / glyph));
      for (const animation of running) animation.cancel();
    }
    strip.style.transform = `translateY(-${to * 5}%)`;
    if (reduced || direction === "none" || typeof strip.animate !== "function" || (from === to && !running.length)) return;
    const [start, end] = rollRange(from, to, direction === "up");
    strip.animate([{ transform: `translateY(-${start * 5}%)` }, { transform: `translateY(-${end * 5}%)` }], { duration, delay, easing: EASE, fill: "backwards" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cell.char]);

  React.useLayoutEffect(() => {
    if (!leaving) return;
    const node = nodeRef.current;
    if (!node || reduced || typeof node.animate !== "function") { onGone(cell.key); return; }
    const width = node.getBoundingClientRect().width;
    const animation = node.animate([{ width: `${width}px`, opacity: 1 }, { width: "0px", opacity: 0 }], { duration: Math.max(1, duration * 0.7), easing: EASE_IN, fill: "forwards" });
    animation.onfinish = () => onGone(cell.key);
    return () => animation.cancel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leaving]);

  return (
    <span ref={nodeRef} data-cell={cell.key} data-kind={cell.kind} className={`relative inline-block overflow-hidden ${digit ? "" : "whitespace-pre"}`}>
      {digit ? (
        <>
          <span className="invisible">0</span>
          <span ref={stripRef} className="absolute inset-x-0 top-0 flex flex-col text-center" style={{ transform: `translateY(-${Number(cell.char) * 5}%)` }}>
            {STRIP.map((glyph, index) => <span key={index} className="block">{glyph}</span>)}
          </span>
        </>
      ) : cell.char}
    </span>
  );
}

type ChipProps = { chip: ChipState; reduced: boolean; valueRef: { current: HTMLSpanElement | null }; onGone: (id: number) => void };

function Chip({ chip, reduced, valueRef, onGone }: ChipProps) {
  const ref = React.useRef(null as HTMLSpanElement | null);
  const [state, setState] = React.useState(chip.animate && !reduced ? "entering" : "settled");
  React.useLayoutEffect(() => {
    const node = ref.current;
    if (!node || !chip.animate || typeof node.animate !== "function") return;
    if (reduced) { const fade = node.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160, easing: "ease-out" }); return () => fade.cancel(); }
    const to = node.getBoundingClientRect();
    const from = valueRef.current ? valueRef.current.getBoundingClientRect() : to;
    const dx = from.left - to.left, dy = from.bottom + 6 - to.top;
    const rest = getComputedStyle(node).boxShadow;
    const lift = "0 10px 20px -10px color-mix(in oklch, var(--color-foreground) 45%, transparent)";
    const animation = node.animate(
      [
        { transform: `translate(${dx}px, ${dy}px) scale(0.92)`, opacity: 0, boxShadow: "0 0 0 0 transparent" },
        { transform: `translate(${dx * 0.35}px, ${dy * 0.35 - 6}px) scale(1.02)`, opacity: 1, boxShadow: lift, offset: 0.55 },
        { transform: "translate(0px, 0px) scale(1)", opacity: 1, boxShadow: rest },
      ],
      { duration: 420, easing: EASE_FLIGHT },
    );
    animation.onfinish = () => setState("settled");
    return () => animation.cancel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  React.useLayoutEffect(() => {
    if (!chip.leaving) return;
    const node = ref.current;
    if (!node || reduced || typeof node.animate !== "function") { onGone(chip.id); return; }
    setState("leaving");
    const out = node.animate([{ transform: "translateX(0px)", opacity: 1 }, { transform: "translateX(12px)", opacity: 0 }], { duration: 200, easing: EASE_IN, fill: "forwards" });
    out.onfinish = () => onGone(chip.id);
    return () => out.cancel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chip.leaving]);
  const Arrow = chip.direction === "up" ? ArrowUpRight : ArrowDownRight;
  return (
    <span ref={ref} data-chip data-trend={chip.trend} data-state={state} aria-hidden="true"
      className={`col-start-1 row-start-1 inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 font-mono text-xs font-medium tabular-nums ${TONE[chip.trend]}`}>
      <Arrow className="size-3" strokeWidth={2.5} />
      {chip.abs && <span>{chip.abs}</span>}
      {chip.abs && chip.pct && <span className="opacity-50">·</span>}
      {chip.pct && <span>{chip.pct}</span>}
    </span>
  );
}

function Ghost({ ghost, reduced, onGone }: { ghost: GhostState; reduced: boolean; onGone: (id: number) => void }) {
  const ref = React.useRef(null as HTMLSpanElement | null);
  React.useLayoutEffect(() => {
    const node = ref.current;
    if (!node || typeof node.animate !== "function") return;
    const animation = reduced
      ? node.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160, easing: "ease-out" })
      : node.animate([{ opacity: 0, transform: "translateY(-4px)" }, { opacity: 1, transform: "translateY(0px)" }], { duration: 300, delay: 120, easing: EASE, fill: "backwards" });
    return () => animation.cancel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  React.useLayoutEffect(() => {
    if (!ghost.out) return;
    const node = ref.current;
    if (!node || typeof node.animate !== "function") { onGone(ghost.id); return; }
    const animation = node.animate([{ opacity: 1 }, { opacity: 0 }], { duration: reduced ? 160 : 400, easing: "ease-in", fill: "forwards" });
    animation.onfinish = () => onGone(ghost.id);
    return () => animation.cancel();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ghost.out]);
  return <span ref={ref} aria-hidden="true" className="inline-block">was {ghost.text}</span>;
}

export function DeltaNumberTicker({
  value, previousValue, format, formatDelta, locale = "en-US", label, goodDirection = "up", showAbsolute = true, showPercent = true, percentDigits = 1,
  ghost: ghostEnabled = true, ghostDuration = 1200, tooltip = true, changedAt, duration = 420, stagger = 20, valueClassName = "text-4xl", onSettle,
  className = "", tabIndex = 0, ref, ...rest
}: DeltaNumberTickerProps) {
  const systemReduced = useReducedMotion();
  const reduced = systemReduced || duration === 0;
  const baseId = React.useId();
  const labelId = `${baseId}-label`, liveId = `${baseId}-live`;
  const formatKey = typeof format === "function" ? format : JSON.stringify(format ?? null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const formatter = React.useMemo(() => createFormatter(format, locale), [formatKey, locale]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const deltaText = React.useMemo(() => formatDelta ?? createDeltaFormatter(format, locale), [formatDelta, formatKey, locale]);
  const percentText = React.useMemo(() => new Intl.NumberFormat(locale, { minimumFractionDigits: percentDigits, maximumFractionDigits: percentDigits, signDisplay: "exceptZero" }), [locale, percentDigits]);
  const layout = React.useMemo(() => buildLayout(value, formatter), [value, formatter]);

  const [chips, setChips] = React.useState([] as ChipState[]);
  const [leaving, setLeaving] = React.useState([] as Cell[]);
  const [ghost, setGhost] = React.useState(null as GhostState | null);
  const [announce, setAnnounce] = React.useState("");
  const [hover, setHover] = React.useState(false);
  const valueRef = React.useRef(null as HTMLSpanElement | null);
  const shownRef = React.useRef(value);
  const cellsRef = React.useRef(layout.byKey);
  const basisRef = React.useRef(previousValue);
  const mountedRef = React.useRef(false);
  const chipId = React.useRef(0);
  const settleTimer = React.useRef(0);
  const ghostTimer = React.useRef(0);

  const shownValue = shownRef.current;
  const previousCells = cellsRef.current;
  const direction: DeltaDirection | "none" = value > shownValue ? "up" : value < shownValue ? "down" : "none";

  const makeChip = (basis: number, animate: boolean): ChipState | null => {
    const delta = value - basis;
    if (delta === 0) return null;
    const abs = showAbsolute ? deltaText(delta) : null;
    const pct = showPercent && basis !== 0 ? `${percentText.format((delta / Math.abs(basis)) * 100)}%` : null;
    if (abs === null && pct === null) return null;
    chipId.current += 1;
    return { id: chipId.current, basis, abs, pct, trend: trendOf(delta, goodDirection), direction: delta > 0 ? "up" : "down", from: formatter.text(basis), leaving: false, animate };
  };
  const sentence = (basis: number) => {
    const delta = value - basis;
    const head = [label, layout.text].filter(Boolean).join(" ");
    if (delta === 0) return head;
    const parts = [`${delta > 0 ? "up" : "down"} ${deltaText(Math.abs(delta)).replace(/^\+/, "")}`];
    if (basis !== 0) parts.push(`${percentText.format(Math.abs(delta / basis) * 100).replace(/^\+/, "")} percent`);
    return `${head}, ${parts.join(", ")}`;
  };

  React.useLayoutEffect(() => {
    const from = shownRef.current;
    shownRef.current = value;
    cellsRef.current = layout.byKey;
    const basisChanged = previousValue !== basisRef.current;
    basisRef.current = previousValue;
    if (!mountedRef.current) {
      mountedRef.current = true;
      setAnnounce(sentence(value));
      if (previousValue !== undefined && previousValue !== value) setChips([makeChip(previousValue, false)].filter((chip): chip is ChipState => chip !== null));
      return;
    }
    if (from === value) {
      if (basisChanged && previousValue !== undefined) { const chip = makeChip(previousValue, false); setChips(chip ? [chip] : []); }
      return;
    }
    const gone = Object.values(previousCells).filter((cell) => !(cell.key in layout.byKey) && (cell.group === "int" || cell.group === "frac"));
    setLeaving((list) => [...list.filter((cell) => !(cell.key in layout.byKey) && !gone.some((other) => other.key === cell.key)), ...(reduced ? [] : gone)]);
    const basis = previousValue !== undefined ? previousValue : from;
    const chip = makeChip(basis, true);
    setChips((list) => [...list.filter((other) => !other.leaving).map((other) => ({ ...other, leaving: true })), ...(chip ? [chip] : [])]);
    if (ghostEnabled) {
      const id = chipId.current + 1000;
      setGhost({ id, text: formatter.text(from), out: false });
      window.clearTimeout(ghostTimer.current);
      ghostTimer.current = window.setTimeout(() => setGhost((current) => (current && current.id === id ? { ...current, out: true } : current)), ghostDuration + (reduced ? 0 : 300));
    }
    setAnnounce(sentence(basis));
    window.clearTimeout(settleTimer.current);
    settleTimer.current = window.setTimeout(() => onSettle?.(value), reduced ? 0 : duration + stagger * Math.max(0, layout.digits - 1));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, previousValue]);

  React.useEffect(() => () => { window.clearTimeout(settleTimer.current); window.clearTimeout(ghostTimer.current); }, []);
  const removeChip = React.useCallback((id: number) => setChips((list) => list.filter((chip) => chip.id !== id)), []);
  const removeCell = React.useCallback((key: string) => setLeaving((list) => list.filter((cell) => cell.key !== key)), []);
  const removeGhost = React.useCallback((id: number) => setGhost((current) => (current && current.id === id ? null : current)), []);

  const current = chips.find((chip) => !chip.leaving) ?? null;
  const leavingInt = leaving.filter((cell) => cell.group === "int").sort((a, b) => indexOf(b.key) - indexOf(a.key));
  const leavingFrac = leaving.filter((cell) => cell.group === "frac").sort((a, b) => indexOf(a.key) - indexOf(b.key));
  const renderCell = (cell: Cell) => <CellView key={cell.key} cell={cell} direction={direction} duration={duration} stagger={stagger} reduced={reduced} grow={!(cell.key in previousCells)} leaving={false} onGone={removeCell} />;
  const renderLeaving = (cell: Cell) => <CellView key={`leaving-${cell.key}`} cell={cell} direction="none" duration={duration} stagger={stagger} reduced={reduced} grow={false} leaving onGone={removeCell} />;
  const lead = layout.cells.filter((cell) => cell.group === "lead");
  const integer = layout.cells.filter((cell) => cell.group === "int");
  const fraction = layout.cells.filter((cell) => cell.group === "frac");
  const tail = layout.cells.filter((cell) => cell.group === "tail");

  return (
    <div ref={ref} {...rest} role="group" aria-labelledby={label ? labelId : undefined} aria-describedby={liveId} tabIndex={tabIndex}
      className={`group relative flex items-center justify-between gap-4 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring ${className}`}>
      <div className="min-w-0">
        {label && <span id={labelId} className="eyebrow block">{label}</span>}
        <span ref={valueRef} aria-hidden="true" className={`relative mt-1 flex items-baseline whitespace-nowrap font-medium leading-[1.2] tracking-tight tabular-nums ${valueClassName}`} style={{ maskImage: MASK, WebkitMaskImage: MASK }}>
          {lead.map(renderCell)}{leavingInt.map(renderLeaving)}{integer.map(renderCell)}{fraction.map(renderCell)}{leavingFrac.map(renderLeaving)}{tail.map(renderCell)}
        </span>
        <span className="block h-4 font-mono text-xs leading-4 text-muted-foreground/70 tabular-nums">
          {ghost && <Ghost key={ghost.id} ghost={ghost} reduced={reduced} onGone={removeGhost} />}
        </span>
      </div>
      <span className="relative inline-grid shrink-0 justify-items-end" onPointerEnter={() => setHover(true)} onPointerLeave={() => setHover(false)}>
        {chips.map((chip) => <Chip key={chip.id} chip={chip} reduced={reduced} valueRef={valueRef} onGone={removeChip} />)}
        {tooltip && current && (
          <span role="tooltip" data-shown={hover ? "true" : "false"} className="pointer-events-none absolute bottom-full right-0 z-10 mb-1.5 whitespace-nowrap rounded-xs bg-bg-3 px-2 py-1 text-[11px] font-medium leading-none text-text-1 opacity-0 transition-opacity duration-150 group-focus-visible:opacity-100 data-[shown=true]:opacity-100">
            from {current.from}{changedAt ? ` at ${changedAt}` : ""}
          </span>
        )}
      </span>
      <span id={liveId} role="status" aria-live="polite" aria-atomic="true" className="sr-only">{announce}</span>
    </div>
  );
}

export default DeltaNumberTicker;
