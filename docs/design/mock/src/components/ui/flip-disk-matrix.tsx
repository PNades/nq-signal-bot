"use client";
// Flip Disk Matrix: 21st.dev id 24220 (daiwiikharihar; the owner's saved docs/design/inspiration/flip-disk-matrix.txt),
// code read 2026-11-15: no network calls. CreamFlow changes (design-system/shortlist.md): the demo's mode buttons and
// clock are gone, it is a display of a given text; lit discs cream/gold instead of lime; glyphs / + - . $ % added;
// the flip without overshoot; `animated` off = Calm (discs snap at once); it updates only when its text changes
// (no rAF loop). Never for risk information (a flip takes 0.6 s; pages/shell.md).
import React, { memo, useMemo } from "react";

const GLYPHS: Record<string, number[]> = {
  "0": [0b01110, 0b10001, 0b10011, 0b10101, 0b11001, 0b10001, 0b01110],
  "1": [0b00100, 0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110],
  "2": [0b01110, 0b10001, 0b00001, 0b00110, 0b01000, 0b10000, 0b11111],
  "3": [0b01110, 0b10001, 0b00001, 0b00110, 0b00001, 0b10001, 0b01110],
  "4": [0b00010, 0b00110, 0b01010, 0b10010, 0b11111, 0b00010, 0b00010],
  "5": [0b11111, 0b10000, 0b11110, 0b00001, 0b00001, 0b10001, 0b01110],
  "6": [0b00110, 0b01000, 0b10000, 0b11110, 0b10001, 0b10001, 0b01110],
  "7": [0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b01000, 0b01000],
  "8": [0b01110, 0b10001, 0b10001, 0b01110, 0b10001, 0b10001, 0b01110],
  "9": [0b01110, 0b10001, 0b10001, 0b01111, 0b00001, 0b00010, 0b01100],
  ":": [0b00000, 0b00100, 0b00000, 0b00000, 0b00000, 0b00100, 0b00000],
  " ": [0, 0, 0, 0, 0, 0, 0],
  "/": [0b00001, 0b00010, 0b00010, 0b00100, 0b01000, 0b01000, 0b10000],
  "+": [0b00000, 0b00100, 0b00100, 0b11111, 0b00100, 0b00100, 0b00000],
  "-": [0b00000, 0b00000, 0b00000, 0b11111, 0b00000, 0b00000, 0b00000],
  ".": [0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b01100, 0b01100],
  "$": [0b00100, 0b01111, 0b10100, 0b01110, 0b00101, 0b11110, 0b00100],
  "%": [0b11001, 0b11010, 0b00010, 0b00100, 0b01000, 0b01011, 0b10011],
  "M": [0b10001, 0b11011, 0b10101, 0b10001, 0b10001, 0b10001, 0b10001],
  "H": [0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001],
};

function bitmap(text: string, cols: number, rows: number): boolean[][] {
  const grid = Array.from({ length: rows }, () => Array<boolean>(cols).fill(false));
  const chars = text.toUpperCase().split("");
  const totalW = chars.length * 6 - 1;
  let ox = Math.max(0, Math.floor((cols - totalW) / 2));
  const oy = Math.max(0, Math.floor((rows - 7) / 2));
  for (const c of chars) {
    const bits = GLYPHS[c] ?? GLYPHS[" "];
    for (let y = 0; y < 7; y++) for (let x = 0; x < 5; x++) if (oy + y < rows && ox + x < cols) grid[oy + y][ox + x] = !!(bits[y] & (1 << (4 - x)));
    ox += 6;
  }
  return grid;
}

const Disk = memo(({ on, animated }: { on: boolean; animated: boolean }) => (
  <div className="relative aspect-square w-full" style={{ perspective: "400px" }}>
    <div
      className="absolute inset-0 size-full"
      style={{
        transformStyle: "preserve-3d",
        transform: on ? "rotateX(180deg)" : "rotateX(0deg)",
        transition: animated ? "transform 600ms cubic-bezier(.3,0,0,1)" : "none",
      }}
    >
      <div className="absolute inset-0 rounded-full border border-white/[.04] bg-[#1c1c1c] shadow-[inset_0_2px_4px_rgba(0,0,0,.6)]" style={{ backfaceVisibility: "hidden" }} />
      <div className="absolute inset-0 rounded-full border border-[#e9c79f] bg-cream shadow-[inset_0_-2px_5px_rgba(120,70,20,.25)]" style={{ backfaceVisibility: "hidden", transform: "rotateX(180deg)" }} />
    </div>
  </div>
));
Disk.displayName = "Disk";

export function FlipDiskDisplay({ text, cols, rows = 9, animated = true, className, label }: {
  text: string; cols?: number; rows?: number; animated?: boolean; className?: string; label?: string;
}) {
  const c = cols ?? text.length * 6 + 3;
  const bits = useMemo(() => bitmap(text, c, rows), [text, c, rows]);
  return (
    <div role="img" aria-label={label ?? text} className={`rounded-md bg-[#090909] p-2 shadow-[inset_0_2px_10px_rgba(0,0,0,1)] ${className ?? ""}`}>
      <div className="grid" style={{ gridTemplateColumns: `repeat(${c}, minmax(0, 1fr))`, gap: "2px" }} aria-hidden="true">
        {bits.map((row, y) => row.map((on, x) => <Disk key={`${x}-${y}`} on={on} animated={animated} />))}
      </div>
    </div>
  );
}

export default FlipDiskDisplay;
