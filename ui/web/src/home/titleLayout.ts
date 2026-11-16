// Geometry of the Home title, shared by the particle title and the scene's nodes and lines, so both always
// lay out from the same numbers. Recomputed whenever the content area changes size (owner 2026-11-14).

/** Padding of the invisible outline the connecting lines end on, as a share of the wordmark's capital-letter
 *  height (owner 2026-11-14: about a quarter, ≈ 45-50 px at 1920 px wide; proportional at every size). The corner
 *  radius equals the padding, so the outline is the letters' box grown outwards by the same distance everywhere. */
export const OUTLINE_PAD_CAP_SHARE = 0.25;
/** The ink box's vertical centre as a share of the scene height (optical centre, a little above the middle). */
const INK_CENTRE_Y = 0.47;
/** Ink grid cell for the line-end search (px). */
const CELL = 4;

export interface TitleLayout {
  W: number; H: number; size: number;
  /** particle targets (every `step` px inside the letters) and whether each is in "Flow" */
  xs: Float32Array; ys: Float32Array; flow: Uint8Array;
  /** the letters' visible bounds and centre */
  box: { left: number; right: number; top: number; bottom: number }; cx: number; cy: number;
  /** distance from (x, y) to the nearest letter pixel is below `r` */
  near: (x: number, y: number, r: number) => boolean;
  /** the invisible rounded outline around the visible letters, where every connecting line ends */
  outline: { left: number; right: number; top: number; bottom: number; r: number; pad: number; capH: number };
  /** signed distance from (x, y) to the outline: negative inside, 0 on it, positive outside */
  outlineDist: (x: number, y: number) => number;
  /** top of the letters in columns x0..x1 */
  topAt: (x0: number, x1: number) => number;
  /** (x, y) is on a letter pixel */
  ink: (x: number, y: number) => boolean;
  /** distance to the nearest letter pixel (2-px grid near the word; 1e6 far away) */
  dist: (x: number, y: number) => number;
  /** middle of the lowercase body (between the baseline and the x-height): the corner lines mirror top-to-bottom
   *  about it, so a top line and the bottom line below it end the same distance from the letters they point at */
  axisY: number;
}

/** Font size: 20% of the scene width, at most 300 px and at most 30% of its height. */
export function titleSize(W: number, H: number) { return Math.max(48, Math.min(W * 0.2, 300, H * 0.3)); }

export function layoutTitle(W: number, H: number, step = 3): TitleLayout {
  W = Math.max(1, Math.round(W)); H = Math.max(1, Math.round(H));
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const ctx = c.getContext("2d", { willReadFrequently: true })!;
  const size = titleSize(W, H);
  const upright = `400 ${size}px "Instrument Serif"`, italic = `italic 400 ${size}px "Instrument Serif"`;
  ctx.textBaseline = "alphabetic";
  ctx.font = upright;
  const mA = ctx.measureText("Cream");
  ctx.font = italic;
  const mB = ctx.measureText("Flow");
  ctx.font = upright;
  const capH = ctx.measureText("H").actualBoundingBoxAscent;   // capital-letter height of the display face
  const xH = ctx.measureText("x").actualBoundingBoxAscent;     // x-height (top of the lowercase body)
  // centre the visible ink (not the advance box) horizontally, and its height around INK_CENTRE_Y
  const inkLeft = -mA.actualBoundingBoxLeft, inkRight = mA.width + mB.actualBoundingBoxRight;
  const x0 = W / 2 - (inkLeft + inkRight) / 2;
  const asc = Math.max(mA.actualBoundingBoxAscent, mB.actualBoundingBoxAscent);
  const desc = Math.max(mA.actualBoundingBoxDescent, mB.actualBoundingBoxDescent);
  const base = H * INK_CENTRE_Y + (asc - desc) / 2;
  ctx.fillStyle = "#fff";
  ctx.font = upright; ctx.fillText("Cream", x0, base);
  ctx.font = italic; ctx.fillText("Flow", x0 + mA.width, base);
  const img = ctx.getImageData(0, 0, W, H).data;

  const xs: number[] = [], ys: number[] = [], flow: number[] = [];
  const gw = Math.ceil(W / CELL), gh = Math.ceil(H / CELL);
  const grid = new Uint8Array(gw * gh);
  let left = W, right = 0, top = H, bottom = 0;
  const colTop = new Int32Array(W).fill(H);
  const mask = new Uint8Array(W * H);
  const split = x0 + mA.width - 4;
  // scan only the rows and columns the text can occupy; every pixel feeds the ink grid, every `step`-th
  // pixel becomes a particle target (so the letters stay as dense as before)
  const y0 = Math.max(0, Math.floor(base - asc) - 2), y1 = Math.min(H, Math.ceil(base + desc) + 3);
  const xa = Math.max(0, Math.floor(x0 + inkLeft) - 2), xb = Math.min(W, Math.ceil(x0 + inkRight) + 3);
  for (let y = y0; y < y1; y++) for (let x = xa; x < xb; x++) {
    if (img[(y * W + x) * 4 + 3] <= 140) continue;
    grid[((y / CELL) | 0) * gw + ((x / CELL) | 0)] = 1;
    if (x < left) left = x; if (x > right) right = x; if (y < top) top = y; if (y > bottom) bottom = y;
    if (y < colTop[x]) colTop[x] = y;
    mask[y * W + x] = 1;
    if (x % step === 0 && y % step === 0) { xs.push(x); ys.push(y); flow.push(x > split ? 1 : 0); }
  }
  /** top of the letters in columns x0..x1 (the shortest-letter case: the "m" under the Live line) */
  const topAt = (xa0: number, xa1: number) => {
    let t = H;
    for (let x = Math.max(0, Math.floor(xa0)); x <= Math.min(W - 1, Math.ceil(xa1)); x++) t = Math.min(t, colTop[x]);
    return t;
  };
  const near = (x: number, y: number, r: number) => {
    const cr = Math.ceil(r / CELL) + 1, gx = (x / CELL) | 0, gy = (y / CELL) | 0;
    for (let j = gy - cr; j <= gy + cr; j++) {
      if (j < 0 || j >= gh) continue;
      for (let i = gx - cr; i <= gx + cr; i++) {
        if (i < 0 || i >= gw || !grid[j * gw + i]) continue;
        // nearest point of the cell to (x, y)
        const qx = Math.min(Math.max(x, i * CELL), i * CELL + CELL), qy = Math.min(Math.max(y, j * CELL), j * CELL + CELL);
        if (Math.hypot(qx - x, qy - y) < r) return true;
      }
    }
    return false;
  };
  /** (x, y) is on a letter pixel */
  const ink = (x: number, y: number) => {
    const xi = Math.round(x), yi = Math.round(y);
    return xi >= 0 && yi >= 0 && xi < W && yi < H && mask[yi * W + xi] === 1;
  };
  const pad = OUTLINE_PAD_CAP_SHARE * capH;
  const o = { left: left - pad, right: right + pad, top: top - pad, bottom: bottom + pad, r: pad, pad, capH };
  const ocx = (o.left + o.right) / 2, ocy = (o.top + o.bottom) / 2;
  const hx = (o.right - o.left) / 2 - o.r, hy = (o.bottom - o.top) / 2 - o.r;
  // signed distance to a rounded rectangle (the standard formula)
  const outlineDist = (x: number, y: number) => {
    const qx = Math.abs(x - ocx) - hx, qy = Math.abs(y - ocy) - hy;
    return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - o.r;
  };
  // distance to the nearest letter pixel, on a 2-px grid around the word (two-pass chamfer); far away = 1e6
  const DR = 2, margin = Math.ceil(capH * 0.9);
  const fx0 = Math.max(0, left - margin), fy0 = Math.max(0, top - margin);
  const fw = Math.ceil((Math.min(W, right + margin) - fx0) / DR) + 1, fh = Math.ceil((Math.min(H, bottom + margin) - fy0) / DR) + 1;
  const field = new Float32Array(fw * fh).fill(1e6);
  for (let j = 0; j < fh; j++) for (let i = 0; i < fw; i++) {
    const px = fx0 + i * DR, py = fy0 + j * DR;
    if (ink(px, py) || ink(px + 1, py) || ink(px, py + 1) || ink(px + 1, py + 1)) field[j * fw + i] = 0;
  }
  const D1 = DR, D2 = DR * Math.SQRT2;
  for (let j = 0; j < fh; j++) for (let i = 0; i < fw; i++) {
    let v = field[j * fw + i];
    if (i > 0) v = Math.min(v, field[j * fw + i - 1] + D1);
    if (j > 0) {
      v = Math.min(v, field[(j - 1) * fw + i] + D1);
      if (i > 0) v = Math.min(v, field[(j - 1) * fw + i - 1] + D2);
      if (i < fw - 1) v = Math.min(v, field[(j - 1) * fw + i + 1] + D2);
    }
    field[j * fw + i] = v;
  }
  for (let j = fh - 1; j >= 0; j--) for (let i = fw - 1; i >= 0; i--) {
    let v = field[j * fw + i];
    if (i < fw - 1) v = Math.min(v, field[j * fw + i + 1] + D1);
    if (j < fh - 1) {
      v = Math.min(v, field[(j + 1) * fw + i] + D1);
      if (i < fw - 1) v = Math.min(v, field[(j + 1) * fw + i + 1] + D2);
      if (i > 0) v = Math.min(v, field[(j + 1) * fw + i - 1] + D2);
    }
    field[j * fw + i] = v;
  }
  const dist = (x: number, y: number) => {
    const i = Math.round((x - fx0) / DR), j = Math.round((y - fy0) / DR);
    return i < 0 || j < 0 || i >= fw || j >= fh ? 1e6 : field[j * fw + i];
  };
  return {
    W, H, size, xs: Float32Array.from(xs), ys: Float32Array.from(ys), flow: Uint8Array.from(flow),
    box: { left, right, top, bottom }, cx: (left + right) / 2, cy: (top + bottom) / 2, near, outline: o, outlineDist, topAt, ink, dist, axisY: base - xH / 2,
  };
}

/**
 * The same title in a scene of height H (banners above Home, owner 2026-11-15): the wordmark keeps the base layout's
 * size and its ink centre stays at INK_CENTRE_Y of the height, so the layout is the base one moved down by
 * (H - base.H) x INK_CENTRE_Y. Exact, and well under a millisecond (no raster), so every banner state is ready at once.
 */
export function shiftLayout(L: TitleLayout, H: number): TitleLayout {
  H = Math.max(1, Math.round(H));
  if (H === L.H) return L;
  const dy = (H - L.H) * INK_CENTRE_Y;
  const ys = new Float32Array(L.ys.length);
  for (let i = 0; i < ys.length; i++) ys[i] = L.ys[i] + dy;
  return {
    ...L, H, ys,
    box: { ...L.box, top: L.box.top + dy, bottom: L.box.bottom + dy }, cy: L.cy + dy,
    outline: { ...L.outline, top: L.outline.top + dy, bottom: L.outline.bottom + dy },
    near: (x, y, r) => L.near(x, y - dy, r), outlineDist: (x, y) => L.outlineDist(x, y - dy),
    topAt: (x0, x1) => L.topAt(x0, x1) + dy, ink: (x, y) => L.ink(x, y - dy), dist: (x, y) => L.dist(x, y - dy),
    axisY: L.axisY + dy,
  };
}

/** Gap between a line's end and the letter directly in front of it, measured back along the line, as a share of the
 *  capital-letter height (owner 2026-11-15: about 60 px at 1440 px wide, proportional at every size; was 45 px). */
export const LINE_LETTER_GAP_CAP_SHARE = 0.35;
/** No part of a line may come closer to any letter than this share of the gap (no running beside the word). */
const CLEARANCE_SHARE = 0.8;
/** For the corner lines only, the clearance floor may step down when nothing clears the higher one. */
const CLEARANCE_TIERS = [0.8, 0.75, 0.7];

export type NodeSlot = "top" | "top-left" | "top-right" | "bottom" | "bottom-left" | "bottom-right";

type Pt = [number, number];
type Cubic = [Pt, Pt, Pt, Pt];

/**
 * The connecting lines, "arcs to the centre" (owner 2026-11-15 picked this of three variants). Every line is a gentle
 * cubic curve from its node to an aim point inside the word, so its last stretch points at the word; it is cut where
 * it would first touch a letter, then shortened by the gap measured back along the line. The aim point is searched
 * near the base aim (a fifth of the word's width either side of the centre for the corner lines, the centre for Live
 * and Settings): the corner pairs take the aim (mirrored left/right) whose two ends mirror best, Live and Settings
 * the one nearest the centre; every candidate must keep the whole line at least 80% of the gap from every letter.
 * The corner lines bow softly and mirror each other; Live and Settings bow slightly, in opposite directions.
 */
const BASE_SHARE = 0.2;

function curveFor(slot: NodeSlot, n: Pt, aim: Pt): Cubic {
  const side = slot.endsWith("left") ? -1 : slot.endsWith("right") ? 1 : 0;
  const vertical = slot.startsWith("top") ? -1 : 1;   // -1: the node is above the word
  const [nx, ny] = n, [ax, ay] = aim, dx = ax - nx, dy = ay - ny, len = Math.hypot(dx, dy);
  // a bow across the chord, mirrored left/right; Live and Settings bow a little, in opposite directions
  const bow = 0.11 * (side === 0 ? (vertical < 0 ? 0.6 : -0.6) : -side * vertical);
  const px = (-dy / len) * bow * len, py = (dx / len) * bow * len;
  return [n, [nx + dx / 3 + px, ny + dy / 3 + py], [nx + (2 * dx) / 3 + px, ny + (2 * dy) / 3 + py], aim];
}

function bez(c: Cubic, t: number): Pt {
  const u = 1 - t, a = u * u * u, b = 3 * u * u * t, d = 3 * u * t * t, e = t * t * t;
  return [a * c[0][0] + b * c[1][0] + d * c[2][0] + e * c[3][0], a * c[0][1] + b * c[1][1] + d * c[2][1] + e * c[3][1]];
}

/** the part of the cubic from 0 to t (de Casteljau) */
function splitLeft(c: Cubic, t: number): Cubic {
  const l = (p: Pt, q: Pt): Pt => [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t];
  const p01 = l(c[0], c[1]), p12 = l(c[1], c[2]), p23 = l(c[2], c[3]);
  const p012 = l(p01, p12), p123 = l(p12, p23), p0123 = l(p012, p123);
  return [c[0], p01, p012, p0123];
}

export interface LineGeometry { d: string; end: Pt; hit: Pt; gapAlong: number; gap: number; clearance: number; aim: Pt }

/** Cut a curve: first letter pixel along it, the end the gap before it, the line's closest approach to any letter. */
function cut(c: Cubic, L: TitleLayout): LineGeometry {
  const N = 900, ts = [0], acc = [0], pts: Pt[] = [c[0]];
  let hit: Pt = c[3];
  for (let k = 1; k <= N; k++) {
    const t = k / N, p = bez(c, t), q = pts[k - 1];
    ts.push(t); pts.push(p); acc.push(acc[k - 1] + Math.hypot(p[0] - q[0], p[1] - q[1]));
    if (L.ink(p[0], p[1])) { hit = p; break; }
  }
  const gap = LINE_LETTER_GAP_CAP_SHARE * L.outline.capH, total = acc[acc.length - 1];
  let i = acc.length - 1;
  while (i > 0 && total - acc[i] < gap) i--;
  let clearance = 1e6;
  for (let k = 0; k <= i; k++) clearance = Math.min(clearance, L.dist(pts[k][0], pts[k][1]));
  const part = splitLeft(c, ts[i]);
  const f = (p: Pt) => p[0].toFixed(1) + "," + p[1].toFixed(1);
  return { d: "M" + f(part[0]) + " C" + f(part[1]) + " " + f(part[2]) + " " + f(part[3]), end: part[3], hit,
    gapAlong: total - acc[i], gap, clearance, aim: c[3] };
}

interface Trace { c: Cubic; pts: Pt[]; ts: number[]; acc: number[]; hitLen: number; hit: Pt }

/** Walk a curve until it first touches a letter: the points, their arc lengths, and the length to that letter. */
function trace(c: Cubic, L: TitleLayout): Trace {
  const N = 900, ts = [0], acc = [0], pts: Pt[] = [c[0]];
  let hit: Pt = c[3];
  for (let k = 1; k <= N; k++) {
    const t = k / N, p = bez(c, t), q = pts[k - 1];
    ts.push(t); pts.push(p); acc.push(acc[k - 1] + Math.hypot(p[0] - q[0], p[1] - q[1]));
    if (L.ink(p[0], p[1])) { hit = p; break; }
  }
  return { c, pts, ts, acc, hitLen: acc[acc.length - 1], hit };
}

/** Cut a traced curve at arc length `len` from its node. */
function cutAt(tr: Trace, len: number, L: TitleLayout): LineGeometry {
  let i = 0;
  while (i < tr.acc.length - 1 && tr.acc[i + 1] <= len) i++;
  let clearance = 1e6;
  for (let k = 0; k <= i; k++) clearance = Math.min(clearance, L.dist(tr.pts[k][0], tr.pts[k][1]));
  const part = splitLeft(tr.c, tr.ts[i]);
  const f = (p: Pt) => p[0].toFixed(1) + "," + p[1].toFixed(1);
  const gap = LINE_LETTER_GAP_CAP_SHARE * L.outline.capH;
  return { d: "M" + f(part[0]) + " C" + f(part[1]) + " " + f(part[2]) + " " + f(part[3]), end: part[3], hit: tr.hit,
    gapAlong: tr.hitLen - tr.acc[i], gap, clearance, aim: tr.c[3] };
}

export interface NodeAt { id: string; slot: NodeSlot; x: number; y: number }

/** Corner nodes sit this share of the word's width either side of its centre ... */
const CORNER_NODE_X_SHARE = 0.48;
/** ... and this many font sizes above / below the middle of the lowercase body: their place at 960 px wide, the
 *  owner's reference (2026-11-15), so the corner lines look the same at every width. */
const CORNER_NODE_Y_SIZES = 1.28;
/** keep a node card inside the scene: its centre at least this far from the left / right edges (px) ... */
const NODE_EDGE_X_PX = 110;
/** ... and between these shares of the scene height */
const NODE_Y_MIN_SHARE = 0.1, NODE_Y_MAX_SHARE = 0.88;

/** Where a corner node sits (px), relative to the wordmark; mirrored left-right and top-bottom. */
export function cornerNodePos(slot: NodeSlot, L: TitleLayout): Pt {
  const wordW = L.box.right - L.box.left;
  const sx = slot.endsWith("left") ? -1 : 1, sy = slot.startsWith("top") ? -1 : 1;
  const dx = Math.min(CORNER_NODE_X_SHARE * wordW, L.W / 2 - NODE_EDGE_X_PX);
  const dy = Math.min(CORNER_NODE_Y_SIZES * L.size, L.axisY - NODE_Y_MIN_SHARE * L.H, NODE_Y_MAX_SHARE * L.H - L.axisY);
  return [L.cx + sx * dx, L.axisY + sy * dy];
}

/** Every node's line, solved on the current layout (solved again on every resize). */
export function solveLines(L: TitleLayout, nodes: NodeAt[]): Record<string, LineGeometry> {
  const g = solveLinesSteps(L, nodes);
  let r = g.next();
  while (!r.done) r = g.next();
  return r.value;
}

/** The same, one candidate aim per step (the identical search and result), so Home can solve the lines of the banner
 *  states ahead of time in small idle slices without holding up a frame (owner 2026-11-15). */
export function* solveLinesSteps(L: TitleLayout, nodes: NodeAt[]): Generator<void, Record<string, LineGeometry>, void> {
  const out: Record<string, LineGeometry> = {};
  const wordW = L.box.right - L.box.left, cx = L.cx, cy = L.cy;
  const ok = (g: LineGeometry) => g.clearance >= CLEARANCE_SHARE * g.gap - 1;
  const at = (s: NodeSlot) => nodes.find((n) => n.slot === s);
  const steps = (centre: number, half: number) => {
    const r: number[] = [];
    for (let k = 0; k <= 60; k++) r.push(centre - half + (2 * half * k) / 60);
    return r;
  };
  // corner lines (owner 2026-11-15): the four mirror each other left-right about the letters' centre and top-bottom
  // about the middle of the lowercase body (axisY), where the letters in front of the lines sit.
  // One curve is built for the bottom-left node and reflected to the other three (the scene puts the top nodes at the
  // vertical mirror of the bottom ones). All four are cut at the same length along the line: the shortest distance to
  // a letter among them minus the gap, so every one keeps at least the gap before the letter in front of it. The aim
  // share is searched near BASE_SHARE for the four distances to agree best, keeping every line clear of other letters
  // (the clearance floor may step down from 80% to 70% of the gap only if nothing clears the higher floor).
  const bl = at("bottom-left"), br = at("bottom-right"), tl = at("top-left"), tr = at("top-right");
  if (bl && br && tl && tr) {
    const gap = LINE_LETTER_GAP_CAP_SHARE * L.outline.capH;
    const ay = L.axisY;
    const mx = (p: Pt): Pt => [2 * cx - p[0], p[1]], my = (p: Pt): Pt => [p[0], 2 * ay - p[1]];
    const map = (c: Cubic, f: (p: Pt) => Pt): Cubic => [f(c[0]), f(c[1]), f(c[2]), f(c[3])];
    // candidates: the aim's share of the word's width, and its height within the lowercase body (the bottom-left aim
    // sits dyShare x-heights below the axis; its top mirror the same amount above)
    const xH = 2 * (L.box.bottom - ay) > 0 ? Math.min(2 * (L.box.bottom - ay), L.outline.capH) : L.outline.capH * 0.6;
    const grid = steps(BASE_SHARE, 0.15).flatMap((s) => [-0.4, -0.2, 0, 0.2, 0.4].map((dyShare) => [s, dyShare] as const));
    const cands: { geo: Record<string, LineGeometry>; clear: number; score: number }[] = [];
    for (const [s, dyShare] of grid) {
      const base = curveFor("bottom-left", [bl.x, bl.y], [cx - s * wordW, ay + dyShare * xH]);
      const curves: Record<string, Cubic> = {
        [bl.id]: base, [br.id]: map(base, mx), [tl.id]: map(base, my), [tr.id]: map(base, (p) => mx(my(p))),
      };
      const trs = Object.fromEntries(Object.entries(curves).map(([id, c]) => [id, trace(c, L)]));
      const lens = Object.values(trs).map((t) => t.hitLen);
      const endLen = Math.min(...lens) - gap;
      const geo = Object.fromEntries(Object.entries(trs).map(([id, t]) => [id, cutAt(t, endLen, L)]));
      const clear = Math.min(...Object.values(geo).map((g) => g.clearance / gap));
      const spread = Math.max(...lens) - Math.min(...lens);
      cands.push({ geo, clear, score: spread + 0.05 * Math.abs(s - BASE_SHARE) * wordW + 0.02 * Math.abs(dyShare) * xH });
      yield;
    }
    let pick: (typeof cands)[number] | undefined;
    for (const floor of CLEARANCE_TIERS) {
      const okc = cands.filter((c) => c.clear >= floor - 1 / gap).sort((a, b) => a.score - b.score);
      if (okc.length) { pick = okc[0]; break; }
    }
    pick ??= cands.sort((a, b) => b.clear - a.clear)[0];
    Object.assign(out, pick.geo);
  }
  // Live and Settings: the valid aim nearest the centre line
  for (const s of ["top", "bottom"] as NodeSlot[]) {
    const n = at(s);
    if (!n) continue;
    const cands = steps(0, 0.12).sort((a, b) => Math.abs(a) - Math.abs(b));
    let pick: LineGeometry | null = null, fallback: LineGeometry | null = null;
    for (const o of cands) {
      const g = cut(curveFor(s, [n.x, n.y], [cx + o * wordW, cy]), L);
      yield;
      if (ok(g)) { pick = g; break; }
      if (!fallback || g.clearance > fallback.clearance) fallback = g;
    }
    out[n.id] = pick ?? fallback!;
  }
  return out;
}
