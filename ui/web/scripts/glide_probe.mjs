// Frame-by-frame node positions on Home while a banner arrives and leaves (used by stage 2f; also runnable alone).
import { chromium } from "playwright";
const BASE = process.env.CF_BASE ?? "http://127.0.0.1:8733";
export async function probe(page) {
  const run = (ms) => page.evaluate((ms) => new Promise((res) => {
    const home = document.querySelector("[data-home]"); const out = []; const t0 = performance.now();
    const tick = () => {
      const nodes = [...home.querySelectorAll("button[aria-label]")].map((b) => { const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; });
      const cv = home.querySelector("canvas"); const cr = cv.getBoundingClientRect();
      // the banner's own animation: the banner bar's height and the first row's opacity
      const bars = document.querySelector("[data-banners]"); const row = bars?.querySelector("[data-banner-id]");
      const banner = [bars ? +bars.getBoundingClientRect().height.toFixed(2) : 0, row ? +(+getComputedStyle(row).opacity).toFixed(3) : 0];
      out.push({ t: performance.now() - t0, nodes, canvas: [cr.left, cr.top, cr.width, cr.height], banner });
      if (performance.now() - t0 < ms) requestAnimationFrame(after); else res(out);
    };
    // measured after each frame is painted (a task queued from the frame's animation step runs after its paint), so
    // the numbers are what was on screen, including the resize callback's hold
    const ch = new MessageChannel(); ch.port1.onmessage = tick;
    const after = () => ch.port2.postMessage(0);
    requestAnimationFrame(after);
  }), ms);
  const summary = (frames) => {
    let maxStep = 0, at = 0;
    for (let i = 1; i < frames.length; i++) for (let k = 0; k < frames[i].nodes.length; k++) {
      const d = Math.hypot(frames[i].nodes[k][0] - frames[i - 1].nodes[k][0], frames[i].nodes[k][1] - frames[i - 1].nodes[k][1]);
      if (d > maxStep) { maxStep = d; at = frames[i].t; }
    }
    const total = frames[0].nodes.map((p, k) => Math.hypot(frames.at(-1).nodes[k][0] - p[0], frames.at(-1).nodes[k][1] - p[1]));
    const moving = frames.filter((f, i) => i && f.nodes.some((p, k) => Math.hypot(p[0] - frames[i - 1].nodes[k][0], p[1] - frames[i - 1].nodes[k][1]) > 0.05)).length;
    // the first painted frame where the banner changed (bar height or row opacity) and where any node had moved
    const b0 = frames[0].banner, n0 = frames[0].nodes;
    const bannerStart = frames.findIndex((f) => Math.abs(f.banner[0] - b0[0]) > 0.05 || Math.abs(f.banner[1] - b0[1]) > 0.001);
    const nodeStart = frames.findIndex((f) => f.nodes.some((p, k) => Math.hypot(p[0] - n0[k][0], p[1] - n0[k][1]) > 0.05));
    // the largest step scaled to a 60-Hz frame (a late frame naturally moves further)
    let maxPerFrame60 = 0;
    for (let i = 1; i < frames.length; i++) { const dt = frames[i].t - frames[i - 1].t; for (let k = 0; k < frames[i].nodes.length; k++) {
      const d = Math.hypot(frames[i].nodes[k][0] - frames[i - 1].nodes[k][0], frames[i].nodes[k][1] - frames[i - 1].nodes[k][1]);
      maxPerFrame60 = Math.max(maxPerFrame60, d * (1000 / 60) / Math.max(dt, 1000 / 60)); } }
    return { frames: frames.length, framesMoving: moving, maxStepPx: +maxStep.toFixed(2), maxStepAtMs: Math.round(at), maxStepPer60HzFramePx: +maxPerFrame60.toFixed(2),
      bannerStartFrame: bannerStart, nodeStartFrame: nodeStart, gapFrames: nodeStart - bannerStart, totalMovePx: total.map((x) => +x.toFixed(1)) };
  };
  const arrive = run(900); await page.locator("[data-flat-all]").click(); const a = await arrive;
  await page.waitForTimeout(500);
  const leave = run(900); await page.getByRole("button", { name: "Got it" }).click(); const l = await leave;
  return { arrive: summary(a), leave: summary(l), arriveFrames: a, leaveFrames: l };
}
if (process.argv[1]?.endsWith("glide_probe.mjs")) {
  const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
  const page = await ctx.newPage(); await page.goto(BASE + "/");
  await page.evaluate(() => sessionStorage.setItem("creamflow.splashShown", "1")); await page.reload(); await page.waitForTimeout(5500); await page.mouse.move(5, 300);
  const r = await probe(page);
  console.log(JSON.stringify({ arrive: r.arrive, leave: r.leave }));
  console.log(r.arriveFrames.slice(0, 8).map((f) => `${f.t} ${f.nodes[0].map((v) => v.toFixed(1))} cv ${f.canvas.map((v) => v.toFixed(1))}`).join("\n"));
  await browser.close();
}

/** Measure one banner event: start sampling, act, return the summary (gap in frames, steps). */
export async function measureEvent(page, act, ms = 900) {
  const run = (ms) => page.evaluate((ms) => new Promise((res) => {
    const home = document.querySelector("[data-home]"); const out = []; const t0 = performance.now();
    const tick = () => {
      const nodes = [...home.querySelectorAll("button[aria-label]")].map((b) => { const r = b.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; });
      const bars = document.querySelector("[data-banners]");
      const rows = [...(bars?.querySelectorAll("[data-banner-id]") ?? [])].map((r) => `${r.getBoundingClientRect().height.toFixed(2)}/${(+getComputedStyle(r).opacity).toFixed(3)}`).join(" ");
      // every line: its cubic sampled at 21 points (screen px), and how far its node end is from its node's point
      const svg = home.querySelector("svg"), sr = svg.getBoundingClientRect();
      const lines = [], ends = [];
      for (const g of home.querySelectorAll("[data-line]")) {
        const v = (g.querySelector("path").getAttribute("d").match(/-?\d+(?:\.\d+)?/g) ?? []).map(Number);
        const P = (t) => { const u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, e = t * t * t;
          return [sr.left + a * v[0] + b * v[2] + c * v[4] + e * v[6], sr.top + a * v[1] + b * v[3] + c * v[5] + e * v[7]]; };
        lines.push(Array.from({ length: 21 }, (_, i) => P(i / 20)));
        const node = home.querySelector(`[data-home-node="${g.getAttribute("data-line")}"]`).getBoundingClientRect();
        ends.push(Math.hypot(sr.left + v[0] - node.left, sr.top + v[1] - node.top));
      }
      out.push({ t: performance.now() - t0, nodes, rows, lines, ends });
      if (performance.now() - t0 < ms) requestAnimationFrame(after); else res(out);
    };
    const ch = new MessageChannel(); ch.port1.onmessage = tick;
    const after = () => ch.port2.postMessage(0);
    requestAnimationFrame(after);
  }), ms);
  const pending = run(ms); await act(); const f = await pending;
  const n0 = f[0].nodes, r0 = f[0].rows;
  const bannerStart = f.findIndex((x) => x.rows !== r0);
  const nodeStart = f.findIndex((x) => x.nodes.some((p, k) => Math.hypot(p[0] - n0[k][0], p[1] - n0[k][1]) > 0.05));
  let maxStep = 0, maxGapMs = 0;
  for (let i = 1; i < f.length; i++) {
    maxGapMs = Math.max(maxGapMs, f[i].t - f[i - 1].t);
    for (let k = 0; k < f[i].nodes.length; k++) maxStep = Math.max(maxStep, Math.hypot(f[i].nodes[k][0] - f[i - 1].nodes[k][0], f[i].nodes[k][1] - f[i - 1].nodes[k][1]));
  }
  // lines: the largest per-frame move of any sampled point on any line, the first frame any line changed, how many
  // frames they changed in, and the largest distance between a line's node end and its node
  let maxLineStep = 0, lineFrames = 0, maxEndOff = 0;
  const l0 = JSON.stringify(f[0].lines);
  const lineStart = f.findIndex((x) => JSON.stringify(x.lines) !== l0);
  for (let i = 0; i < f.length; i++) {
    maxEndOff = Math.max(maxEndOff, ...f[i].ends);
    if (!i) continue;
    let moved = 0;
    f[i].lines.forEach((ln, k) => ln.forEach((pt, j) => { const d = Math.hypot(pt[0] - f[i - 1].lines[k][j][0], pt[1] - f[i - 1].lines[k][j][1]); moved = Math.max(moved, d); }));
    maxLineStep = Math.max(maxLineStep, moved);
    if (moved > 0.05) lineFrames++;
  }
  return { bannerStartFrame: bannerStart, nodeStartFrame: nodeStart, lineStartFrame: lineStart, gapFrames: bannerStart < 0 || nodeStart < 0 ? null : nodeStart - bannerStart,
    lineGapFrames: bannerStart < 0 || lineStart < 0 ? null : lineStart - bannerStart, maxStepPx: +maxStep.toFixed(2), maxLineStepPx: +maxLineStep.toFixed(2),
    lineMovingFrames: lineFrames, maxNodeEndOffPx: +maxEndOff.toFixed(2), longestFrameMs: Math.round(maxGapMs) };
}
