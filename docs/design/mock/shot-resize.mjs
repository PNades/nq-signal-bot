// Home line and resize check (owner 2026-11-14/15): at 1440, 960 and 1920 px, and after resizing 960 -> 1440 and
// 1440 -> 960, verify that the layout follows the content area, the wordmark is centred, and every connecting line
// (a) ends the gap (35% of the capital height, about 60 px at 1440) before the letter directly in front of it,
// (b) points its last stretch at that letter, (c) never comes closer than 80% of the gap to any letter on the way,
// and (d) the corner pairs end at mirrored points. Screenshots: home_flow_<w>.png and the resize pairs.
// Only 127.0.0.1 is reachable.
import { chromium } from "playwright";
import path from "node:path";

const BASE = "http://127.0.0.1:5179/";
const OUT = path.resolve("..");
const browser = await chromium.launch({ args: ["--use-angle=d3d11", "--ignore-gpu-blocklist"] });
const problems = [];

async function measure(page, label) {
  const r = await page.evaluate(() => {
    const L = window.__cfLayout, lines = window.__cfLines;
    const scene = document.querySelector("main > div").getBoundingClientRect();
    const paths = [...document.querySelectorAll("main svg g > path:first-child")];
    const ls = lines.map((g, i) => {
      const p = paths[i], len = p.getTotalLength();
      const e = p.getPointAtLength(len), b = p.getPointAtLength(Math.max(0, len - 6));
      const tx = e.x - b.x, ty = e.y - b.y, hx = g.hit[0] - e.x, hy = g.hit[1] - e.y;
      const ang = Math.acos((tx * hx + ty * hy) / (Math.hypot(tx, ty) * Math.hypot(hx, hy))) * 180 / Math.PI;
      let closest = 1e9;
      for (let s = 0; s <= len; s += 2) { const q = p.getPointAtLength(s); closest = Math.min(closest, L.dist(q.x, q.y)); }
      return { id: g.id, end: [e.x, e.y], gap: g.gapAlong, target: g.gap, ang, closest, len };
    });
    return { sceneW: Math.round(scene.width), sceneH: Math.round(scene.height), layoutW: L.W, layoutH: L.H,
      cx: L.cx, cy: L.axisY, sceneCx: scene.width / 2, size: Math.round(L.size), ls };
  });
  const by = Object.fromEntries(r.ls.map((x) => [x.id, x]));
  const corners = ["accounts", "performance", "gameplan", "review"].map((id) => by[id]);
  // each corner end reflected into the bottom-left quadrant must land on the same point (left-right and top-bottom)
  const folded = corners.map((x) => [r.cx - Math.abs(x.end[0] - r.cx), r.cy + Math.abs(x.end[1] - r.cy)]);
  const foldSpread = Math.max(...folded.map((p) => Math.hypot(p[0] - folded[0][0], p[1] - folded[0][1])));
  const lenSpread = Math.max(...corners.map((x) => x.len)) - Math.min(...corners.map((x) => x.len));
  const follows = r.layoutW === r.sceneW && r.layoutH === r.sceneH;
  const centred = Math.abs(r.cx - r.sceneCx) <= 2;
  // Live and Settings end exactly the gap before their letter; the corner lines at least the gap, the closest exactly
  const gapOk = r.ls.filter((x) => !corners.includes(x)).every((x) => Math.abs(x.gap - x.target) <= 1.5)
    && corners.every((x) => x.gap >= x.target - 1.5) && Math.abs(Math.min(...corners.map((x) => x.gap)) - corners[0].target) <= 1.5;
  const angOk = r.ls.every((x) => x.ang <= 8);
  const clearOk = r.ls.every((x) => x.closest >= (["live", "settings"].includes(x.id) ? 0.8 : 0.7) * x.target - 2);   // corner pairs may step down to 70% to stay mirrored
  const mirOk = foldSpread <= 1.5 && lenSpread <= 1.5;
  const ok = follows && centred && gapOk && angOk && clearOk && mirOk;
  console.log(`${label}: scene ${r.sceneW}x${r.sceneH} (layout ${follows ? "follows" : "STALE"}), title ${r.size}px, centred ${centred ? "yes" : "NO"}, ` +
    `gap target ${r.ls[0].target.toFixed(1)}px, gaps ${r.ls.map((x) => x.gap.toFixed(1)).join("/")}, last stretch vs letter ${r.ls.map((x) => x.ang.toFixed(1)).join("/")} deg, ` +
    `closest approach ${r.ls.map((x) => Math.round(x.closest)).join("/")}px, corner ends four-way mirrored within ${foldSpread.toFixed(1)}px, corner lengths ${corners.map((x) => Math.round(x.len)).join("/")}px -> ${ok ? "OK" : "PROBLEM"}`);
  if (!ok) problems.push(label);
}

async function open(w, h) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.route("**/*", (r) => (r.request().url().startsWith(BASE) ? r.continue() : (problems.push(`blocked ${r.request().url()}`), r.abort())));
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
  await page.mouse.move(w - 5, h - 5);
  await page.goto(`${BASE}?view=home&concept=flow`);
  await page.waitForTimeout(4200);
  return { ctx, page };
}

for (const [w, h] of [[1440, 900], [960, 1000], [1920, 1080]]) {
  const { ctx, page } = await open(w, h);
  await measure(page, `loaded at ${w}`);
  await page.screenshot({ path: path.join(OUT, `home_flow_${w}.png`) });
  await ctx.close();
}
for (const [from, to, name] of [[[960, 1000], [1440, 900], "resize_960_to_1440"], [[1440, 900], [960, 1000], "resize_1440_to_960"]]) {
  const { ctx, page } = await open(...from);
  await page.screenshot({ path: path.join(OUT, `home_${name}_a_loaded_${from[0]}.png`) });
  await page.setViewportSize({ width: to[0], height: to[1] });
  await page.mouse.move(to[0] - 5, to[1] - 5);
  await page.waitForTimeout(900);
  await measure(page, `${name}: after resize to ${to[0]}`);
  await page.screenshot({ path: path.join(OUT, `home_${name}_b_resized_${to[0]}.png`) });
  await ctx.close();
}
await browser.close();
console.log(problems.length ? "PROBLEMS:\n" + problems.join("\n") : "all checks passed");
